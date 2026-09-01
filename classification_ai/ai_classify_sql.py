"""
ai_classify_sql.py
------------------
DBSQL batch-inference version of the ai_classify track.

WHY THIS EXISTS (vs. ai_classify_job.py):
  ai_classify_job.py calls ai_query row-by-row via a PySpark DataFrame on
  *serverless compute*. Its concurrency is bounded by the number of Spark
  partitions, so a scope that lands in a few files (e.g. 4) issues only a few
  ai_query calls at a time and crawls on million-row days.

  This module instead runs the classification as SET-BASED SQL
  (CREATE TABLE AS SELECT ai_query(...)) on a SQL WAREHOUSE, where Databricks'
  ai_query batch inference manages concurrency to the model endpoint directly —
  driving far higher throughput against the same endpoint. (It is still bounded
  by the endpoint's own rate limit; a pay-per-token endpoint has a ceiling, a
  provisioned-throughput endpoint does not.)

PIPELINE (three SQL statements on the warehouse):
  1. <ai_tags_table>__scoped_tmp  = the in-scope rows for the day (English,
                                    audio, customer-side), materialized once.
  2. <ai_tags_table>__bysentence_tmp = one row per DISTINCT sentence with the
                                    LLM's category array. The only paid,
                                    model-bound step (dedup = pay per unique
                                    sentence, not per row).
  3. <ai_tags_table>              = fan the tags back to ALL in-scope rows,
                                    pivoted to one 0/1 column per category
                                    (leaves + rolled-up parents) — same shape as
                                    the rule engine / compare job expect.
  Then the two temp tables are dropped.

HIERARCHY ROLL-UP IN SQL:
  The LLM returns leaf (comparison-target) category NAMES. A parent category is
  "on" when any target descendant is returned. We precompute, per category, the
  set of target names that should turn it on (itself if it is a target, plus any
  target descendant), then the 0/1 column is
  arrays_overlap(ai_categories, array(<those names>)) — no UDF, pure SQL.

RUN: as a Databricks notebook/job task. Requires a SQL warehouse id
(warehouse_id) and a Model-Serving-supported region. Uses the Databricks SDK's
Statement Execution API to submit the batch SQL to that warehouse.
"""
import os
import sys

CATALOG = "daria_krasavina"
SCHEMA = "gm_voc"
_NS = "%s.%s" % (CATALOG, SCHEMA)

DEFAULTS = {
    "sentence_table": _NS + ".qualtrics_audio_transcripts_sentence_level_sample_data",
    "ai_tags_table": _NS + ".voc_classification_ai_tags",
    # AI track runs on a SINGLE calendar day (empty = no date filter).
    "classify_date": "2026-06-11",
    "classify_endpoint": "databricks-meta-llama-3-3-70b-instruct",
    # 0 = all distinct sentences for the day. Set small (e.g. 10000) for a
    # throughput-measurement / cost-preview run first.
    "sample_limit": "0",
    # REQUIRED for run(): the SQL warehouse that executes the batch SQL.
    "warehouse_id": "",
}

TEXT_FIELD = "words"
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# Reuse the exact prompt + category model the PySpark job uses, so results are
# comparable across the two implementations.
from ai_classify_job import build_prompt, load_categories


def _trigger_names():
    """Return ([(category_id, [target_names_that_turn_it_on]) ...], prompt).

    For each category we list the comparison-target names whose presence should
    set the category's 0/1 column: the category's own name if it is a target,
    plus the names of any target categories that are its descendants (roll-up).
    """
    target_labels, name_to_id, all_ids, ancestors, meta = load_categories()
    target_names = set(target_labels.keys())
    cols = []
    for cid in all_ids:
        names = set()
        if meta[cid]["name"] in target_names:
            names.add(meta[cid]["name"])
        for d in all_ids:                      # d is a descendant of cid?
            if cid in ancestors.get(d, []) and meta[d]["name"] in target_names:
                names.add(meta[d]["name"])
        cols.append((cid, sorted(names)))
    return cols, build_prompt(target_labels)


def _sql_array(names):
    """SQL array('a','b') literal (single quotes escaped), or None if empty."""
    if not names:
        return None
    return "array(%s)" % ", ".join("'%s'" % n.replace("'", "\\'") for n in names)


def build_statements(params):
    """Build the SQL steps. The prompt is returned separately and passed as a
    bound parameter (:prompt) so its quotes/newlines never touch the SQL text."""
    cols, prompt = _trigger_names()

    where = ("lower(language) = 'english' AND lower(id_source) = 'audio' "
             "AND lower(verbatimtype) = 'clientverbatim' "
             "AND %s IS NOT NULL AND length(trim(%s)) > 0" % (TEXT_FIELD, TEXT_FIELD))
    day = (params.get("classify_date") or "").strip()
    if day:
        where += " AND to_date(document_date) = '%s'" % day
    limit = int(params.get("sample_limit") or 0)
    lim = ("LIMIT %d" % limit) if limit > 0 else ""

    ep = params["classify_endpoint"]
    sent = params["sentence_table"]
    tags = params["ai_tags_table"]
    scoped_tmp = tags + "__scoped_tmp"
    bysentence_tmp = tags + "__bysentence_tmp"

    s_scoped = (
        "CREATE OR REPLACE TABLE %s AS "
        "SELECT natural_id, id_document, id_verbatim, document_date, %s "
        "FROM %s WHERE %s" % (scoped_tmp, TEXT_FIELD, sent, where))

    # The paid step: one ai_query per DISTINCT sentence. Set-based so the SQL
    # warehouse's batch inference drives concurrency to the endpoint.
    s_bysentence = (
        "CREATE OR REPLACE TABLE %s AS "
        "SELECT %s, from_json(CAST(ai_query('%s', concat(:prompt, %s)) AS STRING), "
        "'array<string>') AS ai_categories "
        "FROM (SELECT DISTINCT %s FROM %s %s)"
        % (bysentence_tmp, TEXT_FIELD, ep, TEXT_FIELD, TEXT_FIELD, scoped_tmp, lim))

    col_exprs = []
    for cid, names in cols:
        arr = _sql_array(names)
        if arr:
            col_exprs.append(
                "CASE WHEN arrays_overlap(b.ai_categories, %s) THEN 1 ELSE 0 END AS `%s`"
                % (arr, cid))
        else:                                   # no target descendant -> always 0
            col_exprs.append("CAST(0 AS INT) AS `%s`" % cid)

    # Fan the per-sentence tags back to ALL in-scope rows (INNER join keeps the
    # run consistent when sample_limit < full: only classified sentences appear).
    s_final = (
        "CREATE OR REPLACE TABLE %s AS "
        "SELECT s.natural_id, s.id_document, s.id_verbatim, s.document_date, s.%s, "
        "to_json(b.ai_categories) AS ai_categories, %s "
        "FROM %s s JOIN %s b ON s.%s = b.%s"
        % (tags, TEXT_FIELD, ", ".join(col_exprs),
           scoped_tmp, bysentence_tmp, TEXT_FIELD, TEXT_FIELD))

    drops = ["DROP TABLE IF EXISTS %s" % bysentence_tmp,
             "DROP TABLE IF EXISTS %s" % scoped_tmp]
    return {"prompt": prompt, "scoped": s_scoped, "bysentence": s_bysentence,
            "final": s_final, "drops": drops,
            "scoped_tmp": scoped_tmp, "bysentence_tmp": bysentence_tmp}


def get_params():
    params = dict(DEFAULTS)
    try:
        from pyspark.dbutils import DBUtils
        from pyspark.sql import SparkSession
        dbutils = DBUtils(SparkSession.builder.getOrCreate())
        for k in DEFAULTS:
            try:
                dbutils.widgets.text(k, DEFAULTS[k])
                v = dbutils.widgets.get(k)
                if v:
                    params[k] = v
            except Exception:
                pass
    except Exception:
        pass
    for arg in sys.argv[1:]:
        if arg.startswith("--") and "=" in arg:
            k, v = arg[2:].split("=", 1)
            if k in params:
                params[k] = v
    return params


def run():
    """Execute the batch SQL on the configured SQL warehouse."""
    import time

    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.sql import (
        ExecuteStatementRequestOnWaitTimeout, StatementParameterListItem)

    params = get_params()
    print("Params: %s" % params)
    wid = (params.get("warehouse_id") or "").strip()
    if not wid:
        raise ValueError("warehouse_id is required (the SQL warehouse to run the "
                         "batch ai_query on). Pass it as a job parameter / widget.")

    st = build_statements(params)
    w = WorkspaceClient()

    def execute(sql, parameters=None, label=""):
        t0 = time.time()
        r = w.statement_execution.execute_statement(
            warehouse_id=wid, statement=sql, parameters=parameters,
            wait_timeout="50s",
            on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CONTINUE)
        while r.status.state.value in ("PENDING", "RUNNING"):
            time.sleep(5)
            r = w.statement_execution.get_statement(r.statement_id)
        if r.status.state.value != "SUCCEEDED":
            raise RuntimeError("statement failed (%s): %s" % (label, r.status.error))
        print("  %-10s done in %.1fs" % (label, time.time() - t0))
        return r

    print("Running DBSQL batch classification on warehouse %s ..." % wid)
    execute(st["scoped"], label="scoped")
    execute(st["bysentence"],
            parameters=[StatementParameterListItem(name="prompt", value=st["prompt"])],
            label="classify")
    execute(st["final"], label="fan-out")
    for d in st["drops"]:
        execute(d, label="cleanup")
    print("Wrote %s" % params["ai_tags_table"])


if __name__ == "__main__":
    run()
