"""
ai_classify_job.py
------------------
Lightweight AI-powered topic classification for the GM VOC POC using Databricks
built-in AI functions — the "AI-assisted classification" track that complements
the deterministic rule engine (voc_topic_model_job.py).

This is a genuinely different approach: instead of hand-maintained keyword rules,
it uses a large language model (via `ai_classify`) to decide which topic a
customer sentence belongs to, steered by each topic's business definition. It
also attaches sentiment (`ai_analyze_sentiment`). No rules to maintain — the
model generalizes to paraphrases the rules would miss.

WHY THIS EXISTS ALONGSIDE THE RULE ENGINE:
  - Rule engine  = faithful, deterministic replica of GM's XM Discover control.
  - AI classify  = lighter-weight ML alternative; no keyword upkeep, handles
                   novel phrasing, but non-deterministic and per-call cost.
  compare_approaches_job.py measures where they agree/disagree.

REQUIREMENTS (per Databricks docs):
  - Serverless SQL warehouse or serverless notebook/job compute.
  - Databricks Runtime 18.2+ and a Model-Serving-supported region.
  - AI functions are NOT available on classic clusters / Pro-Classic warehouses.

INPUT / SCOPE:
  Same two tables and join as the rule job. Scope is applied in SQL here
  (English + audio + customer-side + date range) — a lighter filter than the
  rule engine's full boilerplate exclusion, which is fine for an ML baseline.

OUTPUT (Delta):
  <ai_tags_table>: one row per in-scope sentence with
    ai_topic            - the single best-matching topic label (or "None")
    ai_confidence        - confidence score when available
    sentiment            - positive / negative / neutral / mixed
"""
import json
import os
import sys

DEFAULTS = {
    "sentence_table": "daria_krasavina.gm_voc.qualtrics_audio_transcripts_sentence_level_sample_data",
    "metadata_table": "daria_krasavina.gm_voc.qualtrics_audio_transcripts_metadata_sample_data",
    "ai_tags_table": "daria_krasavina.gm_voc.voc_ai_topic_tags",
    "date_start": "2025-07-01",
    "date_end": "2026-06-30",
    # ai_classify options; version pinned for deterministic behavior.
    "ai_version": "2.1",
    # Cap rows for a cost-bounded POC run; set to 0 for the full corpus.
    "sample_limit": "5000",
}

JOIN_KEY = "natural_id"
TEXT_FIELD = "words"

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def find_rules_json():
    """Locate shared/rules.json regardless of folder layout (see tagger.py)."""
    repo = os.path.dirname(HERE)
    for c in (os.path.join(HERE, "rules.json"),
              os.path.join(repo, "shared", "rules.json"),
              os.path.join(HERE, "shared", "rules.json"),
              os.path.join(os.getcwd(), "rules.json"),
              os.path.join(os.getcwd(), "shared", "rules.json")):
        if os.path.exists(c):
            return c
    raise FileNotFoundError("rules.json not found near %s" % HERE)


# "None" catch-all so the classifier can decline all four topics (most
# customer sentences match no POC topic).
NONE_LABEL = "None of these"


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


def build_label_map(rules_path=None):
    """Label -> business description, taken from the same rules.json the rule
    engine uses, so both approaches classify against identical topic definitions.
    """
    if rules_path is None:
        rules_path = find_rules_json()
    with open(rules_path) as f:
        rules = json.load(f)
    labels = {}
    for node in rules["nodes"]:
        if node.get("comparison_target"):
            desc = (node.get("description") or node["name"]).strip()
            # Trim overly long definitions to keep the prompt lean.
            labels[node["name"]] = desc[:900]
    labels[NONE_LABEL] = (
        "The sentence does not clearly relate to advisor confusion, inaccurate "
        "information, or GM rewards points / redemption.")
    return labels


def run():
    from pyspark.sql import SparkSession

    spark = SparkSession.builder.appName("gm_voc_ai_classify").getOrCreate()
    params = get_params()
    print("Params: %s" % params)

    label_map = build_label_map()
    labels_json = json.dumps(label_map, ensure_ascii=False)
    # SQL-escape single quotes for embedding inside the query string.
    labels_sql = labels_json.replace("'", "''")

    limit = int(params.get("sample_limit") or 0)
    limit_clause = ("LIMIT %d" % limit) if limit > 0 else ""

    # In-scope customer verbatims, joined to metadata (kept minimal). The rule
    # engine's full boilerplate exclusion is intentionally not reproduced here;
    # this is the ML baseline over customer-side English audio.
    scoped_sql = """
        SELECT s.{jk} AS natural_id, s.id_document, s.id_verbatim,
               s.document_date, s.{text} AS words
        FROM {sent} s
        WHERE lower(s.language) = 'english'
          AND lower(s.id_source) = 'audio'
          AND lower(s.verbatimtype) = 'clientverbatim'
          AND to_date(s.document_date) BETWEEN '{ds}' AND '{de}'
          AND s.{text} IS NOT NULL AND length(trim(s.{text})) > 0
        {limit}
    """.format(jk=JOIN_KEY, text=TEXT_FIELD, sent=params["sentence_table"],
               ds=params["date_start"], de=params["date_end"], limit=limit_clause)

    # ai_classify returns the best label; ai_analyze_sentiment adds sentiment.
    # options map pins the function version and requests confidence scores.
    classify_sql = """
        SELECT
            natural_id, id_document, id_verbatim, document_date, words,
            ai_classify(
                words,
                '{labels}',
                map('version', '{ver}', 'enableConfidenceScores', 'true')
            ) AS ai_raw,
            ai_analyze_sentiment(words) AS sentiment
        FROM scoped
    """.format(labels=labels_sql, ver=params["ai_version"])

    spark.sql(scoped_sql).createOrReplaceTempView("scoped")
    classified = spark.sql(classify_sql)
    classified.createOrReplaceTempView("classified")

    # ai_classify v2.x returns a struct/variant; extract the top label + score
    # defensively so this works whether ai_raw is a plain string (v1) or a
    # struct with response[0].{value,confidence_score} (v2.x).
    extract_sql = """
        SELECT
            natural_id, id_document, id_verbatim, document_date, words,
            sentiment,
            CASE
              WHEN typeof(ai_raw) = 'string' THEN CAST(ai_raw AS STRING)
              ELSE CAST(try_element_at(ai_raw:response, 1):value AS STRING)
            END AS ai_topic,
            CASE
              WHEN typeof(ai_raw) = 'string' THEN NULL
              ELSE CAST(try_element_at(ai_raw:response, 1):confidence_score AS DOUBLE)
            END AS ai_confidence
        FROM classified
    """
    try:
        result = spark.sql(extract_sql)
        result.write.mode("overwrite").format("delta") \
            .option("overwriteSchema", "true").saveAsTable(params["ai_tags_table"])
    except Exception as e:
        # Fallback: some runtimes return ai_classify as a plain string. Persist
        # the raw output so nothing is lost, and surface the parse issue.
        print("Struct extraction failed (%s); writing raw output instead." % e)
        classified.write.mode("overwrite").format("delta") \
            .option("overwriteSchema", "true").saveAsTable(params["ai_tags_table"])

    print("Wrote %s" % params["ai_tags_table"])
    spark.sql("""
        SELECT ai_topic, sentiment, count(*) AS n
        FROM {t} GROUP BY ai_topic, sentiment ORDER BY n DESC
    """.format(t=params["ai_tags_table"])).show(50, truncate=False)


if __name__ == "__main__":
    run()
