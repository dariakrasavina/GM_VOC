# Databricks notebook source
# MAGIC %md
# MAGIC # Qualtrics Validation — sentence-level comparison of each tagging method vs Qualtrics
# MAGIC
# MAGIC Compares each classification method (rule engine, ai_classify, ai_query) against the
# MAGIC **Qualtrics control output** at the **sentence grain**, using `sentence_id` as the join key,
# MAGIC and writes precision / recall / F1 per method × leaf to three result tables.
# MAGIC
# MAGIC **Why this notebook exists:** the tagging output tables dropped `sentence_id` (they keep
# MAGIC natural_id / id_verbatim / id_document / words). Qualtrics is keyed by `Sentence_ID`, so to
# MAGIC compare at true sentence grain we first **backfill `sentence_id`** onto each method's tags by
# MAGIC joining the SOURCE sentence table (which has `sentence_id`) to a per-distinct-sentence tag map
# MAGIC (keyed by `words`). Driving from the source — one unique `sentence_id` per row — avoids the
# MAGIC ~4.6% duplicate-text fan-out that a reverse join would cause.
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

src         = dbutils.widgets.get("source_table")
qtab        = dbutils.widgets.get("qualtrics_table")
out         = dbutils.widgets.get("out_schema")
ai_classify = dbutils.widgets.get("ai_classify_tags")
ai_query    = dbutils.widgets.get("ai_query_tags")
rule        = dbutils.widgets.get("rule_tags")
day         = dbutils.widgets.get("classify_date")
hs, he      = int(dbutils.widgets.get("hour_start")), int(dbutils.widgets.get("hour_end"))

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
# MAGIC ## Step 1 — Backfill `sentence_id` onto each method's tags (source-driven map-join)
# MAGIC Rebuilds `<method>_sid` tables: SOURCE sentences (unique `sentence_id`) LEFT-joined to a
# MAGIC `words -> tags` map from each method's output. `ai_classify`/`rule` = full day; `ai_query` = the window.

# COMMAND ----------
def backfill(method_tags, out_sid, extra_map_col, scope_where):
    extra_map = f", MAX({extra_map_col}) {extra_map_col}" if extra_map_col else ""
    extra_sel = f", m.{extra_map_col}" if extra_map_col else ""
    spark.sql(f"""
      CREATE OR REPLACE TABLE {out}.{out_sid} AS
      WITH map AS (
        SELECT words,
          MAX(cc_advisor_confusing_makes_no_sense) cc_advisor_confusing_makes_no_sense,
          MAX(cc_advisor_inaccurate_information)   cc_advisor_inaccurate_information,
          MAX(points_redeem)                       points_redeem{extra_map}
        FROM {method_tags}
        GROUP BY words)
      SELECT src.sentence_id, src.natural_id, src.id_document, src.id_verbatim, src.document_date, src.words,
        m.cc_advisor_confusing_makes_no_sense, m.cc_advisor_inaccurate_information, m.points_redeem{extra_sel}
      FROM {src} src JOIN map m ON src.words = m.words
      WHERE {scope_where} AND {SCOPE}""")
    n = spark.table(f"{out}.{out_sid}").count()
    print(f"  {out_sid}: {n:,} rows")

backfill(ai_classify, "voc_classification_ai_classify_tags_full_day_sid", "ai_labels",     DAY)
backfill(ai_query,    "voc_classification_ai_query_sql_tags_sid",         "ai_categories", WIN)
# rule: map only from the validation day so the full-year table isn't scanned
rule_day = f"(SELECT * FROM {rule} WHERE to_timestamp(document_date) >= '{day_start}' AND to_timestamp(document_date) < {day_next})"
backfill(rule_day,    "voc_classification_rule_tags_20260611_sid",        None,            DAY)

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
