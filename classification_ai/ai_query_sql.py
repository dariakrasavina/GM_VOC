"""
ai_query_sql.py
------------------
DBSQL batch-inference version of the ai_query classification track.

WHY BATCH SQL (vs. a per-partition PySpark approach):
  Calling ai_query row-by-row via a PySpark DataFrame on serverless compute
  bounds concurrency by the number of Spark partitions, so a scope that lands in
  a few files issues only a few ai_query calls at a time and crawls on
  million-row days.

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
    "ai_tags_table": _NS + ".voc_classification_ai_query_sql_tags",
    # AI track runs on a SINGLE calendar day (empty = no date filter).
    "classify_date": "2026-06-11",
    # Optional hour-of-day window within that day (based on the document_date
    # timestamp string, e.g. "...T09:.."). Both empty = whole day. hour_end is
    # EXCLUSIVE, so hour_start=9, hour_end=12 means 09:00:00-11:59:59.
    "hour_start": "",
    "hour_end": "",
    "classify_endpoint": "databricks-claude-sonnet-4-6",
    # Number of sentences to classify. 0 = all.
    "sample_limit": "0",
    # QUALITY KNOBS (added to fix over-tagging of short filler on real data):
    # min_words   - drop sentences with fewer than N words before classifying
    #               (0 = off). Removes 1-2 word filler like "What?" / "Huh.".
    # sample_mode - "random" (default; representative sample for a fair read) or
    #               "frequency" (top-N most-common = max row coverage for a capped
    #               PRODUCTION run; over-samples short filler, so not for eval).
    #               Moot when sample_limit=0 (all distinct sentences classified).
    # context_window - 0 = classify each DISTINCT sentence once (dedup, cheap).
    #               N>0 = classify each ROW with +/-N neighbor sentences from the
    #               same call as context (NO dedup -> more calls; for small
    #               quality-eval samples, not full-day scale).
    "min_words": "0",
    "sample_mode": "random",
    "context_window": "0",
    # REQUIRED for run(): the SQL warehouse that executes the batch SQL.
    "warehouse_id": "",
}

TEXT_FIELD = "words"
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# Reuse the exact prompt + category model the PySpark job uses, so results are
# comparable across the two implementations.
from ai_common import build_prompt, load_categories


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
    # Optional hour-of-day window. The hour is read straight from the ISO
    # timestamp STRING (chars 12-13, e.g. "2026-06-11T09:..") so it matches the
    # hour you see in document_date (no timezone conversion). hour_end exclusive.
    hs = (params.get("hour_start") or "").strip()
    he = (params.get("hour_end") or "").strip()
    if hs != "" and he != "":
        where += (" AND cast(substring(document_date, 12, 2) AS INT) >= %d"
                  " AND cast(substring(document_date, 12, 2) AS INT) < %d"
                  % (int(hs), int(he)))
    # (2) content pre-filter: drop sentences shorter than min_words words.
    min_words = int(params.get("min_words") or 0)
    if min_words > 0:
        where += " AND size(split(trim(%s), ' ')) >= %d" % (TEXT_FIELD, min_words)
    limit = int(params.get("sample_limit") or 0)
    lim = ("LIMIT %d" % limit) if limit > 0 else ""
    sample_mode = (params.get("sample_mode") or "frequency").strip().lower()
    ctx_n = int(params.get("context_window") or 0)

    ep = params["classify_endpoint"]
    sent = params["sentence_table"]
    tags = params["ai_tags_table"]
    scoped_tmp = tags + "__scoped_tmp"

    s_scoped = (
        "CREATE OR REPLACE TABLE %s AS "
        "SELECT natural_id, id_document, id_verbatim, sentence_id, document_date, %s "
        "FROM %s WHERE %s" % (scoped_tmp, TEXT_FIELD, sent, where))

    # Per-category 0/1 columns (roll-up compiled to arrays_overlap), aliased to
    # whichever table holds ai_categories.
    def _col_exprs(alias):
        out = []
        for cid, names in cols:
            arr = _sql_array(names)
            if arr:
                out.append("CASE WHEN arrays_overlap(%s.ai_categories, %s) "
                           "THEN 1 ELSE 0 END AS `%s`" % (alias, arr, cid))
            else:
                out.append("CAST(0 AS INT) AS `%s`" % cid)
        return ", ".join(out)

    if ctx_n > 0:
        # (4) CONTEXT MODE: classify each ROW with +/-ctx_n neighbor sentences from
        # the same call as context. No dedup (context makes rows distinct), so this
        # is for small quality-eval samples, not full-day scale. sample_mode=random
        # gives a representative sample; frequency doesn't apply per-row.
        byrow_tmp = tags + "__byrow_tmp"
        win = "PARTITION BY id_verbatim ORDER BY document_date"
        parts = ["lag(%s, %d) OVER (%s)" % (TEXT_FIELD, i, win) for i in range(ctx_n, 0, -1)]
        parts += ["'>>>'", TEXT_FIELD, "'<<<'"]
        parts += ["lead(%s, %d) OVER (%s)" % (TEXT_FIELD, i, win) for i in range(1, ctx_n + 1)]
        ctx_expr = "concat_ws(' ', %s)" % ", ".join(parts)
        order = "ORDER BY rand()" if sample_mode == "random" else ""
        s_bysentence = (
            "CREATE OR REPLACE TABLE %s AS "
            "SELECT natural_id, id_document, id_verbatim, sentence_id, document_date, %s, "
            "from_json(CAST(ai_query('%s', concat(:prompt, ctx)) AS STRING), "
            "'array<string>') AS ai_categories "
            "FROM (SELECT natural_id, id_document, id_verbatim, sentence_id, document_date, %s, "
            "%s AS ctx FROM %s %s %s)"
            % (byrow_tmp, TEXT_FIELD, ep, TEXT_FIELD, ctx_expr, scoped_tmp, order, lim))
        s_final = (
            "CREATE OR REPLACE TABLE %s AS "
            "SELECT b.natural_id, b.id_document, b.id_verbatim, b.sentence_id, b.document_date, b.%s, "
            "to_json(b.ai_categories) AS ai_categories, %s FROM %s b"
            % (tags, TEXT_FIELD, _col_exprs("b"), byrow_tmp))
        drops = ["DROP TABLE IF EXISTS %s" % byrow_tmp,
                 "DROP TABLE IF EXISTS %s" % scoped_tmp]
        return {"prompt": prompt, "scoped": s_scoped, "bysentence": s_bysentence,
                "final": s_final, "drops": drops}

    # DEDUP MODE: one ai_query per DISTINCT sentence, then fan the tag back to all
    # rows that share it. Set-based so the warehouse's batch inference drives
    # concurrency. (3) sample_mode selects which distinct sentences:
    #   frequency -> most-common first (max row coverage per call)
    #   random    -> representative sample (fair quality read)
    bysentence_tmp = tags + "__bysentence_tmp"
    if sample_mode == "random":
        inner = ("SELECT %s FROM (SELECT DISTINCT %s FROM %s) ORDER BY rand() %s"
                 % (TEXT_FIELD, TEXT_FIELD, scoped_tmp, lim))
    else:
        inner = ("SELECT %s FROM %s GROUP BY %s ORDER BY count(*) DESC %s"
                 % (TEXT_FIELD, scoped_tmp, TEXT_FIELD, lim))
    s_bysentence = (
        "CREATE OR REPLACE TABLE %s AS "
        "SELECT %s, from_json(CAST(ai_query('%s', concat(:prompt, %s)) AS STRING), "
        "'array<string>') AS ai_categories FROM (%s)"
        % (bysentence_tmp, TEXT_FIELD, ep, TEXT_FIELD, inner))
    s_final = (
        "CREATE OR REPLACE TABLE %s AS "
        "SELECT s.natural_id, s.id_document, s.id_verbatim, s.sentence_id, s.document_date, s.%s, "
        "to_json(b.ai_categories) AS ai_categories, %s "
        "FROM %s s JOIN %s b ON s.%s = b.%s"
        % (tags, TEXT_FIELD, _col_exprs("b"),
           scoped_tmp, bysentence_tmp, TEXT_FIELD, TEXT_FIELD))
    drops = ["DROP TABLE IF EXISTS %s" % bysentence_tmp,
             "DROP TABLE IF EXISTS %s" % scoped_tmp]
    return {"prompt": prompt, "scoped": s_scoped, "bysentence": s_bysentence,
            "final": s_final, "drops": drops}


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
