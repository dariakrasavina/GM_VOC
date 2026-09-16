# Databricks notebook source
# MAGIC %md
# MAGIC # Qualtrics Validation — sentence-level comparison of each tagging method vs Qualtrics
# MAGIC
# MAGIC Compares each classification method (rule engine, ai_classify, ai_query) against the
# MAGIC **Qualtrics control output** at the **sentence grain**, using `sentence_id` as the join key,
# MAGIC and writes precision / recall / F1 per method × leaf to three result tables.
# MAGIC
# MAGIC **Why this notebook exists:** the tagging output tables dropped `sentence_id` (they keep
# MAGIC natural_id / id_document / id_verbatim / document_date / words). Qualtrics is keyed by
# MAGIC `Sentence_ID`, so to compare at true sentence grain we first **backfill `sentence_id`** onto
# MAGIC each method's tags. Step 1 does this by pairing each result-table row to a source
# MAGIC `sentence_id` with `row_number()` within the composite key, so each `_sid` table keeps
# MAGIC **exactly** its result table's row count with a **unique** `sentence_id` (see Step 1 for the
# MAGIC why — the composite key alone collides ~8% of the time). The permanent fix is to re-run the
# MAGIC tagging jobs, which now emit `sentence_id` natively.
# MAGIC
# MAGIC Scope is defined by the `sentence_id` join (each method's `_sid` table is its own population),
# MAGIC so no timezone-sensitive date filtering of Qualtrics is needed. Qualtrics is the ground truth:
# MAGIC FP = method tagged / Qualtrics didn't; FN = Qualtrics tagged / method missed.

# COMMAND ----------
# Parameters. Defaults point at the GM client test tables; override via job params / widgets.
dbutils.widgets.text("source_table",     "marketing_prod.silver_voice_of_customer_gbl.qualtrics_audio_transcripts_sentence_level")
dbutils.widgets.text("qualtrics_table",  "marketing_test.silver_voice_of_customer_gmna.qualtrics_raw_outputs")
dbutils.widgets.text("out_schema",       "marketing_test.silver_voice_of_customer_gmna")
dbutils.widgets.text("ai_classify_tags", "marketing_test.silver_voice_of_customer_gmna.voc_classification_ai_classify_tags_full_day")
dbutils.widgets.text("ai_query_tags",    "marketing_test.silver_voice_of_customer_gmna.voc_classification_ai_query_sql_tags")
dbutils.widgets.text("rule_tags",        "marketing_test.silver_voice_of_customer_gmna.voc_classification_rule_tags_v2")
dbutils.widgets.text("classify_date",    "2026-06-11")   # the day Qualtrics covers
dbutils.widgets.text("hour_start",       "9")            # ai_query ran only this window
dbutils.widgets.text("hour_end",         "12")
# Step 1 (the sentence_id backfill) scans the full source + sorts, so it is the slow,
# expensive step. It only needs to re-run when the tagging tables are refreshed. Default
# "false": skip it and run the (fast) comparisons on the existing _sid tables. Set "true"
# to rebuild the _sid tables (ideally on the SQL warehouse, where the sort is quick).
dbutils.widgets.text("rebuild_sid",      "false")

src         = dbutils.widgets.get("source_table")
qtab        = dbutils.widgets.get("qualtrics_table")
out         = dbutils.widgets.get("out_schema")
ai_classify = dbutils.widgets.get("ai_classify_tags")
ai_query    = dbutils.widgets.get("ai_query_tags")
rule        = dbutils.widgets.get("rule_tags")
day         = dbutils.widgets.get("classify_date")
hs, he      = int(dbutils.widgets.get("hour_start")), int(dbutils.widgets.get("hour_end"))
REBUILD_SID = dbutils.widgets.get("rebuild_sid").strip().lower() in ("true", "1", "yes")

day_start = f"{day} 00:00:00"
day_next  = "date_add(to_date('%s'), 1)" % day
DAY  = f"to_timestamp(src.document_date) >= '{day_start}' AND to_timestamp(src.document_date) < {day_next}"
WIN  = (f"to_timestamp(src.document_date) >= '{day} {hs:02d}:00:00' "
        f"AND to_timestamp(src.document_date) < '{day} {he:02d}:00:00'")
SCOPE = "lower(src.id_source)='audio' AND lower(src.verbatimtype)='clientverbatim'"

# The three POC leaf categories: our column id  <->  Qualtrics Category_Name
LEAVES = [
    ("cc_advisor_confusing_makes_no_sense", "CC Advisor - Confusing/Makes No Sense"),
    ("cc_advisor_inaccurate_information",   "CC Advisor - Inaccurate Information"),
    ("points_redeem",                       "Points - Redeem"),
]

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 1 — Backfill `sentence_id` onto each method's tags (row-number pairing)
# MAGIC **Opt-in — runs only when `rebuild_sid=true`** (this is the slow step: full source scan + sort).
# MAGIC A normal run skips it and uses the existing `_sid` tables; rebuild only after the tagging
# MAGIC tables are refreshed. Rebuilds `<method>_sid` tables so each has **exactly the same row count**
# MAGIC as its result table (one row in, one row out) and a **unique `sentence_id`** per row.
# MAGIC
# MAGIC **Why not join on `words`:** the result tables never stored `sentence_id`; they carry
# MAGIC `natural_id, id_document, id_verbatim, document_date, words`. But `document_date` is
# MAGIC *document*-level (all sentences in a call share it) and the only per-sentence discriminator
# MAGIC in the source (`cb_conv_sentence_start_time_ms`) was not carried into the result tables, so
# MAGIC that composite key still collides (~8% of rows) wherever identical short text repeats in one
# MAGIC call. Joining on it (or on `words` alone) fans out / re-populates from the source and inflates
# MAGIC the row count by ~3%.
# MAGIC
# MAGIC **Approach:** build a source lookup keyed by the composite key with a `row_number()` **within
# MAGIC each key** (`_voc_src_keyed_tmp`), then drive **from the result table** (row_number within the
# MAGIC same key) and join on `(composite key, rn)`. This is an injective pairing: N identical-text
# MAGIC rows in a call map to N distinct source `sentence_id`s. Count is preserved exactly; within a
# MAGIC collision group the specific pairing is arbitrary, but those rows are identical text and carry
# MAGIC identical tags, so validation metrics are unaffected. (The permanent fix is to re-run the
# MAGIC tagging jobs, which now emit `sentence_id` natively — then no backfill is needed.)

# COMMAND ----------
# Only rebuild the _sid tables when rebuild_sid=true (they change only when the tagging
# tables are refreshed). A normal run skips this and uses the existing _sid tables, so the
# job just recomputes the fast comparisons below.
if not REBUILD_SID:
    print("rebuild_sid=false -> skipping Step 1; using existing _sid tables. "
          "Set rebuild_sid=true (ideally on a SQL warehouse) after refreshing the tagging tables.")
else:
    # Build the per-sentence keyed source ONCE (one scan), then reuse for all three methods.
    KEY = "natural_id, id_document, id_verbatim, document_date, words"
    src_keyed = f"{out}._voc_src_keyed_tmp"
    spark.sql(f"""
      CREATE OR REPLACE TABLE {src_keyed} AS
      SELECT src.sentence_id, src.natural_id, src.id_document, src.id_verbatim, src.document_date, src.words,
        row_number() OVER (PARTITION BY src.natural_id, src.id_document, src.id_verbatim,
                                        src.document_date, src.words ORDER BY src.sentence_id) AS rn
      FROM {src} src
      WHERE {DAY} AND {SCOPE} AND lower(src.language)='english'""")

    def backfill(method_tags, out_sid, extra_col, day_filter=""):
        # Drive FROM the result table so the _sid row count == the result-table row count exactly.
        extra_sel_t = f", {extra_col}" if extra_col else ""
        extra_sel   = f", t.{extra_col}" if extra_col else ""
        where = f"WHERE {day_filter}" if day_filter else ""
        spark.sql(f"""
          CREATE OR REPLACE TABLE {out}.{out_sid} AS
          WITH t AS (
            SELECT natural_id, id_document, id_verbatim, document_date, words,
              cc_advisor_confusing_makes_no_sense, cc_advisor_inaccurate_information, points_redeem{extra_sel_t},
              row_number() OVER (PARTITION BY {KEY} ORDER BY words) AS rn
            FROM {method_tags} {where})
          SELECT s.sentence_id, t.natural_id, t.id_document, t.id_verbatim, t.document_date, t.words,
            t.cc_advisor_confusing_makes_no_sense, t.cc_advisor_inaccurate_information, t.points_redeem{extra_sel}
          FROM t LEFT JOIN {src_keyed} s
            ON t.natural_id=s.natural_id AND t.id_document=s.id_document AND t.id_verbatim=s.id_verbatim
           AND t.document_date=s.document_date AND t.words=s.words AND t.rn=s.rn""")
        n = spark.table(f"{out}.{out_sid}").count()
        print(f"  {out_sid}: {n:,} rows")

    # ai_classify / ai_query result tables are already scoped to the day / window.
    backfill(ai_classify, "voc_classification_ai_classify_tags_full_day_sid", "ai_labels")
    backfill(ai_query,    "voc_classification_ai_query_sql_tags_sid",         "ai_categories")
    # rule_tags_v2 spans the full corpus -> restrict to the validation day.
    backfill(rule,        "voc_classification_rule_tags_20260611_sid",        None,
             day_filter=f"to_date(document_date)='{day}'")
    spark.sql(f"DROP TABLE IF EXISTS {src_keyed}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 2 — Coverage check (how many Qualtrics sentences each method covers)

# COMMAND ----------
display(spark.sql(f"""
WITH q AS (SELECT DISTINCT CAST(Sentence_ID AS BIGINT) sid FROM {qtab})
SELECT
 (SELECT count(*) FROM q) qualtrics_distinct_sentences,
 (SELECT count(*) FROM q WHERE sid IN (SELECT sentence_id FROM {out}.voc_classification_ai_classify_tags_full_day_sid)) in_ai_classify,
 (SELECT count(*) FROM q WHERE sid IN (SELECT sentence_id FROM {out}.voc_classification_ai_query_sql_tags_sid))         in_ai_query_window,
 (SELECT count(*) FROM q WHERE sid IN (SELECT sentence_id FROM {out}.voc_classification_rule_tags_20260611_sid))        in_rule
"""))

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 3 — Comparisons vs Qualtrics -> write result tables
# MAGIC One row per method × leaf: qualtrics/method positives, TP / FP / FN, precision / recall / F1.

# COMMAND ----------
def compare_and_write(method_sid, method_label, out_table):
    # Qualtrics pivoted to sentence x leaf, then LEFT-joined from the method population.
    qcase = ", ".join(
        f"MAX(CASE WHEN Category_Name='{disp}' THEN 1 ELSE 0 END) q_{i}"
        for i, (_, disp) in enumerate(LEAVES))
    jsel = ", ".join(
        f"m.{col} m_{i}, COALESCE(q.q_{i},0) q_{i}" for i, (col, _) in enumerate(LEAVES))
    blocks = []
    for i, (col, _) in enumerate(LEAVES):
        blocks.append(f"""
         SELECT '{method_label}' method, '{col}' leaf_category,
           sum(q_{i}) qualtrics_positives, sum(m_{i}) method_positives, sum(m_{i}*q_{i}) true_positives,
           sum(CASE WHEN m_{i}=1 AND q_{i}=0 THEN 1 ELSE 0 END) false_positives_method_only,
           sum(CASE WHEN m_{i}=0 AND q_{i}=1 THEN 1 ELSE 0 END) false_negatives_qualtrics_only,
           round(100.0*sum(m_{i}*q_{i})/nullif(sum(m_{i}),0),1) precision_pct,
           round(100.0*sum(m_{i}*q_{i})/nullif(sum(q_{i}),0),1) recall_pct,
           round(100.0*2*sum(m_{i}*q_{i})/nullif(sum(m_{i})+sum(q_{i}),0),1) f1_pct,
           current_timestamp() computed_at, {i} ord FROM j""")
    spark.sql(f"""
      CREATE OR REPLACE TABLE {out}.{out_table} AS
      WITH q AS (SELECT CAST(Sentence_ID AS BIGINT) sid, {qcase} FROM {qtab} GROUP BY CAST(Sentence_ID AS BIGINT)),
      j AS (SELECT {jsel} FROM {out}.{method_sid} m LEFT JOIN q ON m.sentence_id = q.sid)
      SELECT method, leaf_category, qualtrics_positives, method_positives, true_positives,
             false_positives_method_only, false_negatives_qualtrics_only,
             precision_pct, recall_pct, f1_pct, computed_at
      FROM ({" UNION ALL ".join(blocks)}) ORDER BY ord""")
    print(f"  wrote {out}.{out_table}")

compare_and_write("voc_classification_ai_classify_tags_full_day_sid", "ai_classify",  "validation_qualtrics_vs_ai_classify")
compare_and_write("voc_classification_ai_query_sql_tags_sid",         "ai_query",     "validation_qualtrics_vs_ai_query")
compare_and_write("voc_classification_rule_tags_20260611_sid",        "rule_engine",  "validation_qualtrics_vs_rule_engine")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 4 — Sentence-by-sentence side-by-side (all four methods on one row)
# MAGIC One row per sentence, joined on **sentence_id + natural_id + id_verbatim**, with each leaf
# MAGIC shown four ways: `<leaf>_qualtrics`, `<leaf>_rule`, `<leaf>_ai_classify`, `<leaf>_ai_query`.
# MAGIC This is the row-level view ("how did the same sentence get tagged?") behind the aggregate
# MAGIC precision/recall tables above. The spine is the full-day `ai_classify` population (every
# MAGIC in-scope sentence). `ai_query` only ran on the `hour_start`-`hour_end` window, so its columns
# MAGIC are NULL outside it — `ai_query_ran` = 1 flags the rows where all four methods are populated.
# MAGIC
# MAGIC (Joining on all three keys was verified to drop nothing vs `sentence_id` alone: `sentence_id`
# MAGIC is globally unique per sentence, so `natural_id`/`id_verbatim` act as a correctness guard.)

# COMMAND ----------
# Short, readable per-leaf name for the four side-by-side columns.
SHORT = {"cc_advisor_confusing_makes_no_sense": "confusing",
         "cc_advisor_inaccurate_information":   "inaccurate",
         "points_redeem":                       "points"}
KEYS = ("CAST(%s AS STRING) sid, trim(CAST(%s AS STRING)) nid, trim(CAST(%s AS STRING)) vid")

def sentence_by_sentence(out_table="validation_sentence_by_sentence"):
    # Qualtrics pivoted to one row per (sentence, natural, verbatim), 0/1 per leaf.
    qcase = ", ".join(
        f"MAX(CASE WHEN Category_Name='{disp}' THEN 1 ELSE 0 END) q_{SHORT[col]}"
        for col, disp in LEAVES)
    # Each method CTE: cast the three keys + rename its leaf cols with a method prefix.
    # The spine (with_words) also carries the source words + document_date for display.
    def cte(sid_table, pfx, with_words=False):
        leaves = ", ".join(f"{col} {pfx}_{SHORT[col]}" for col, _ in LEAVES)
        extra  = ", words, document_date" if with_words else ""
        return (f"SELECT {KEYS % ('sentence_id','natural_id','id_verbatim')}{extra}, "
                f"{leaves} FROM {out}.{sid_table}")
    # Final projection: for each leaf, the four methods side by side.
    #   qualtrics / rule -> COALESCE to 0. A NULL there is only an artifact of how those
    #     sources are stored (Qualtrics keeps positives-only rows; the rule engine persists
    #     only in-scope rows), NOT "sentence absent" — both evaluated every sentence, so
    #     "not tagged" is 0.
    #   ai_query -> left as-is. ai_query only ran on the hour_start-hour_end window, so a
    #     NULL genuinely means "ai_query never processed this sentence" (see document_date_ts
    #     and the ai_query_ran flag). Forcing it to 0 would misrepresent "not run" as "said no".
    proj = ", ".join(
        f"COALESCE(q.q_{SHORT[col]},0) AS {SHORT[col]}_qualtrics, "
        f"COALESCE(r.r_{SHORT[col]},0) AS {SHORT[col]}_rule, "
        f"a.a_{SHORT[col]} AS {SHORT[col]}_ai_classify, "
        f"y.y_{SHORT[col]} AS {SHORT[col]}_ai_query"
        for col, _ in LEAVES)
    spark.sql(f"""
      CREATE OR REPLACE TABLE {out}.{out_table} AS
      WITH q AS (SELECT {KEYS % ('Sentence_ID','Natural_Id','Verbatim_ID')}, {qcase}
                 FROM {qtab} GROUP BY 1,2,3),
      a AS ({cte('voc_classification_ai_classify_tags_full_day_sid', 'a', with_words=True)}),
      r AS ({cte('voc_classification_rule_tags_20260611_sid',        'r')}),
      y AS ({cte('voc_classification_ai_query_sql_tags_sid',         'y')})
      SELECT a.sid AS sentence_id, a.nid AS natural_id, a.vid AS id_verbatim, a.words,
        to_timestamp(a.document_date) AS document_date_ts,
        {proj},
        CASE WHEN y.sid IS NULL THEN 0 ELSE 1 END AS ai_query_ran,
        current_timestamp() AS computed_at
      FROM a
      LEFT JOIN r ON a.sid=r.sid AND a.nid=r.nid AND a.vid=r.vid
      LEFT JOIN y ON a.sid=y.sid AND a.nid=y.nid AND a.vid=y.vid
      LEFT JOIN q ON a.sid=q.sid AND a.nid=q.nid AND a.vid=q.vid""")
    print(f"  wrote {out}.{out_table} ({spark.table(f'{out}.{out_table}').count():,} rows)")

sentence_by_sentence()

# COMMAND ----------
# MAGIC %md
# MAGIC ### Peek: rows where the four methods disagree (within the ai_query window)

# COMMAND ----------
display(spark.sql(f"""
  SELECT document_date_ts, substr(words,1,80) sentence,
    confusing_qualtrics, confusing_rule, confusing_ai_classify, confusing_ai_query,
    points_qualtrics, points_rule, points_ai_classify, points_ai_query
  FROM {out}.validation_sentence_by_sentence
  WHERE ai_query_ran=1
    AND (confusing_qualtrics+confusing_rule+confusing_ai_classify+confusing_ai_query IN (1,2,3)
      OR points_qualtrics+points_rule+points_ai_classify+points_ai_query IN (1,2,3))
  LIMIT 50"""))

# COMMAND ----------
# MAGIC %md
# MAGIC ## Results

# COMMAND ----------
display(spark.sql(f"""
  SELECT * FROM {out}.validation_qualtrics_vs_rule_engine
  UNION ALL SELECT * FROM {out}.validation_qualtrics_vs_ai_query
  UNION ALL SELECT * FROM {out}.validation_qualtrics_vs_ai_classify
  ORDER BY method, leaf_category"""))
