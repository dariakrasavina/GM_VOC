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
    # Keep this small for smoke tests — each row is a paid LLM call.
    "sample_limit": "200",
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
            desc = (node.get("description") or node["name"])
            # Collapse newlines/tabs/repeated spaces to single spaces. The raw
            # workbook descriptions contain newlines, which corrupt the JSON
            # string ai_classify parses internally (AI_FUNCTION_COMPILATION_ERROR).
            desc = " ".join(desc.split())
            # Trim overly long definitions to keep the prompt lean.
            labels[node["name"]] = desc[:900]
    labels[NONE_LABEL] = (
        "The sentence does not clearly relate to advisor confusion, inaccurate "
        "information, or GM rewards points / redemption.")
    return labels


def run():
    from pyspark.sql import SparkSession
    from pyspark.sql import functions as F

    spark = SparkSession.builder.appName("gm_voc_ai_classify").getOrCreate()
    params = get_params()
    print("Params: %s" % params)

    label_map = build_label_map()
    labels_json = json.dumps(label_map, ensure_ascii=False)

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

    scoped = spark.sql(scoped_sql)

    # This runtime's ai_classify wants `labels` as a STRING (a JSON array of
    # label names), not an ARRAY column ("labels requires STRING, but got ARRAY").
    # Pass the JSON via F.lit() so PySpark handles escaping — the topic names are
    # short and contain no newlines, so the JSON stays valid. ai_classify returns
    # the chosen label as a plain STRING (no struct to parse). ai_analyze_sentiment
    # adds sentiment (positive/negative/neutral/mixed).
    label_names = list(label_map.keys())
    labels_json = json.dumps(label_names, ensure_ascii=False)

    # ai_classify returns a VARIANT on this runtime; cast to STRING so the column
    # is orderable/groupable and stores cleanly for the comparison job
    # (VARIANT can't be used in GROUP BY -> GROUP_EXPRESSION_TYPE_IS_NOT_ORDERABLE).
    result = (scoped
        .withColumn("_labels", F.lit(labels_json))
        .withColumn("ai_topic", F.expr("CAST(ai_classify(words, _labels) AS STRING)"))
        .withColumn("sentiment", F.expr("CAST(ai_analyze_sentiment(words) AS STRING)"))
        .drop("_labels")
        .select("natural_id", "id_document", "id_verbatim", "document_date",
                "words", "ai_topic", "sentiment"))

    result.write.mode("overwrite").format("delta") \
        .option("overwriteSchema", "true").saveAsTable(params["ai_tags_table"])

    print("Wrote %s" % params["ai_tags_table"])
    spark.sql("""
        SELECT ai_topic, sentiment, count(*) AS n
        FROM {t} GROUP BY ai_topic, sentiment ORDER BY n DESC
    """.format(t=params["ai_tags_table"])).show(50, truncate=False)


if __name__ == "__main__":
    run()
