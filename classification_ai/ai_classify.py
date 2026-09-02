"""
ai_classify.py
----------------------
Third AI classification variant for the bake-off: uses the Databricks BUILT-IN
ai_classify() SQL function (SINGLE-label) instead of ai_query + a custom prompt
(multi-label, see ai_query_sql.py / ai_query_job.py).

  ai_classify(text, ARRAY(labels))  -> the ONE best-matching label.

Because ai_classify always picks one of the labels you give it, we add an
explicit "None of the above" label so a sentence that fits no category can opt
out — otherwise every row would be force-tagged with one of the four. The chosen
leaf label is rolled up to its parent categories, so the output has the SAME
shape (one 0/1 column per category, leaves + parents) as the other tracks and the
compare job lines up.

TRADE-OFFS vs the ai_query approach:
  + Simpler: no prompt to maintain; native function.
  + Single-label matches "tag each row with one of the 4 where applicable".
  - No category DEFINITIONS to steer it (ai_classify takes bare label names), so
    accuracy may differ — evaluate via the compare job.
  - Same model backend => SAME throughput ceiling. This is a different METHOD,
    not a fix for the rate-limit problem.

Runs as DBSQL batch on a SQL warehouse (same execution path as ai_query_sql).
"""
import os
import sys

CATALOG = "daria_krasavina"
SCHEMA = "gm_voc"
_NS = "%s.%s" % (CATALOG, SCHEMA)

DEFAULTS = {
    "sentence_table": _NS + ".qualtrics_audio_transcripts_sentence_level_sample_data",
    "ai_tags_table": _NS + ".voc_classification_ai_classify_tags",
    "classify_date": "2026-06-11",
    # Distinct sentences to classify, taken MOST-FREQUENT-FIRST so a partial run
    # covers the largest share of the day's rows. 0 = all distinct.
    "sample_limit": "0",
    # REQUIRED for run(): the SQL warehouse that executes the batch SQL.
    "warehouse_id": "",
}

# Explicit opt-out label so ai_classify can decline to tag a sentence.
NONE_LABEL = "None of the above"

TEXT_FIELD = "words"
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from ai_query_job import load_categories


def _columns_and_labels():
    """Return ([(category_id, [target_names_that_turn_it_on]) ...], [leaf_labels]).

    Same roll-up logic as the other tracks: a category's column is 1 when the
    chosen label is the category itself (if a target) or any target descendant.
    leaf_labels is the set of comparison-target names ai_classify chooses among.
    """
    target_labels, name_to_id, all_ids, ancestors, meta = load_categories()
    target_names = list(target_labels.keys())
    cols = []
    for cid in all_ids:
        names = set()
        if meta[cid]["name"] in target_names:
            names.add(meta[cid]["name"])
        for d in all_ids:                       # d is a descendant of cid?
            if cid in ancestors.get(d, []) and meta[d]["name"] in target_names:
                names.add(meta[d]["name"])
        cols.append((cid, sorted(names)))
    return cols, target_names


def _sql_str_list(names):
    """SQL comma-separated quoted list: 'a', 'b' (single quotes escaped)."""
    return ", ".join("'%s'" % n.replace("'", "\\'") for n in names)


def build_statements(params):
    cols, leaf_labels = _columns_and_labels()

    where = ("lower(language) = 'english' AND lower(id_source) = 'audio' "
             "AND lower(verbatimtype) = 'clientverbatim' "
             "AND %s IS NOT NULL AND length(trim(%s)) > 0" % (TEXT_FIELD, TEXT_FIELD))
    day = (params.get("classify_date") or "").strip()
    if day:
        where += " AND to_date(document_date) = '%s'" % day
    limit = int(params.get("sample_limit") or 0)
    lim = ("LIMIT %d" % limit) if limit > 0 else ""

    sent = params["sentence_table"]
    tags = params["ai_tags_table"]
    scoped_tmp = tags + "__scoped_tmp"
    bysentence_tmp = tags + "__bysentence_tmp"

    # Labels ai_classify chooses among: the 4 targets + an explicit opt-out.
    labels_sql = _sql_str_list(leaf_labels + [NONE_LABEL])

    s_scoped = (
        "CREATE OR REPLACE TABLE %s AS "
        "SELECT natural_id, id_document, id_verbatim, document_date, %s "
        "FROM %s WHERE %s" % (scoped_tmp, TEXT_FIELD, sent, where))

    # The paid step: one ai_classify() per DISTINCT sentence -> one label string.
    # FREQUENCY-PRIORITIZED (same as ai_query_sql): order distinct sentences by how
    # many rows they cover (count DESC) so sample_limit = N tags the largest share
    # of the day's rows for N calls. limit = 0 classifies every distinct sentence.
    s_bysentence = (
        "CREATE OR REPLACE TABLE %s AS "
        "SELECT %s, ai_classify(%s, array(%s)) AS ai_label "
        "FROM (SELECT %s FROM %s GROUP BY %s ORDER BY count(*) DESC %s)"
        % (bysentence_tmp, TEXT_FIELD, TEXT_FIELD, labels_sql, TEXT_FIELD, scoped_tmp,
           TEXT_FIELD, lim))

    col_exprs = []
    for cid, names in cols:
        if names:                               # single-label: chosen label IN triggers
            col_exprs.append(
                "CASE WHEN b.ai_label IN (%s) THEN 1 ELSE 0 END AS `%s`"
                % (_sql_str_list(names), cid))
        else:
            col_exprs.append("CAST(0 AS INT) AS `%s`" % cid)

    s_final = (
        "CREATE OR REPLACE TABLE %s AS "
        "SELECT s.natural_id, s.id_document, s.id_verbatim, s.document_date, s.%s, "
        "b.ai_label, %s "
        "FROM %s s JOIN %s b ON s.%s = b.%s"
        % (tags, TEXT_FIELD, ", ".join(col_exprs),
           scoped_tmp, bysentence_tmp, TEXT_FIELD, TEXT_FIELD))

    drops = ["DROP TABLE IF EXISTS %s" % bysentence_tmp,
             "DROP TABLE IF EXISTS %s" % scoped_tmp]
    return {"scoped": s_scoped, "bysentence": s_bysentence, "final": s_final,
            "drops": drops}


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
    import time

    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.sql import ExecuteStatementRequestOnWaitTimeout

    params = get_params()
    print("Params: %s" % params)
    wid = (params.get("warehouse_id") or "").strip()
    if not wid:
        raise ValueError("warehouse_id is required (the SQL warehouse to run the "
                         "batch ai_classify on). Pass it as a job parameter / widget.")

    st = build_statements(params)
    w = WorkspaceClient()

    def execute(sql, label=""):
        t0 = time.time()
        r = w.statement_execution.execute_statement(
            warehouse_id=wid, statement=sql, wait_timeout="50s",
            on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CONTINUE)
        while r.status.state.value in ("PENDING", "RUNNING"):
            time.sleep(5)
            r = w.statement_execution.get_statement(r.statement_id)
        if r.status.state.value != "SUCCEEDED":
            raise RuntimeError("statement failed (%s): %s" % (label, r.status.error))
        print("  %-10s done in %.1fs" % (label, time.time() - t0))
        return r

    print("Running DBSQL batch ai_classify() on warehouse %s ..." % wid)
    execute(st["scoped"], label="scoped")
    execute(st["bysentence"], label="classify")
    execute(st["final"], label="fan-out")
    for d in st["drops"]:
        execute(d, label="cleanup")
    print("Wrote %s" % params["ai_tags_table"])


if __name__ == "__main__":
    run()
