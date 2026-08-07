"""
ai_classify_job.py
------------------
Lightweight AI-powered topic classification for the GM VOC POC using Databricks
built-in AI functions — the "AI-assisted classification" track that complements
the deterministic rule engine (voc_topic_model_job.py).

This is a genuinely different approach from the rule engine: instead of
hand-maintained keyword rules, an LLM decides which categories a sentence
belongs to, steered by each category's business definition. It also attaches
sentiment (`ai_analyze_sentiment`).

MULTI-LABEL + ROLL-UP (matches XM Discover and the rule engine):
  A sentence can belong to *several* categories, so we ask the LLM (via
  `ai_query`) for the JSON array of ALL applicable categories, not a single best
  label. When a category matches, its ancestors are also assigned (roll-up), so
  parent categories (e.g. Loyalty, Contact Center) get counts from their
  children — exactly like the rule engine's output. Output is one boolean column
  per category, so the comparison job can compare like-for-like.

WHY THIS EXISTS ALONGSIDE THE RULE ENGINE:
  - Rule engine = faithful, deterministic replica of GM's XM Discover control.
  - ai_classify = lighter-weight ML alternative; no keyword upkeep, handles
                  novel phrasing, but non-deterministic and per-call cost.
  compare_approaches_job.py measures where they agree/disagree, per category.

REQUIREMENTS (per Databricks docs):
  - Serverless compute, Databricks Runtime 18.2+, Model-Serving region.

OUTPUT (Delta):
  <ai_tags_table>: one row per in-scope sentence with
    <category_id>        - 1/0 per category (leaves + rolled-up parents)
    ai_categories        - the raw category list the LLM returned
    sentiment            - positive / negative / neutral / mixed
"""
import json
import os
import sys

DEFAULTS = {
    "sentence_table": "daria_krasavina.gm_voc.qualtrics_audio_transcripts_sentence_level_sample_data",
    "metadata_table": "daria_krasavina.gm_voc.qualtrics_audio_transcripts_metadata_sample_data",
    "ai_tags_table": "daria_krasavina.gm_voc.voc_classification_ai_tags",
    "date_start": "2025-07-01",
    "date_end": "2026-06-30",
    # LLM endpoint used for multi-label classification via ai_query.
    "classify_endpoint": "databricks-meta-llama-3-3-70b-instruct",
    # Cap rows for a cost-bounded POC run; set to 0 for the full corpus.
    # Keep this small for smoke tests — each row is a paid LLM call.
    "sample_limit": "200",
}

JOIN_KEY = "natural_id"
TEXT_FIELD = "words"

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def find_category_model():
    """Locate shared/category_model.json regardless of folder layout (see tagger.py)."""
    repo = os.path.dirname(HERE)
    for c in (os.path.join(HERE, "category_model.json"),
              os.path.join(repo, "shared", "category_model.json"),
              os.path.join(HERE, "shared", "category_model.json"),
              os.path.join(os.getcwd(), "category_model.json"),
              os.path.join(os.getcwd(), "shared", "category_model.json")):
        if os.path.exists(c):
            return c
    raise FileNotFoundError("category_model.json not found near %s" % HERE)


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


def load_categories(rules_path=None):
    """Return category metadata from the same category_model.json the rule engine
    uses, so both classifiers work against identical categories.

    Returns:
      target_labels : {category_name: short description}  (the leaf categories
                      the LLM chooses among)
      name_to_id    : {category_name: category_id}         (all categories)
      all_ids       : [category_id ...] in model order      (leaves + parents)
      ancestors     : {category_id: [ancestor_id ...]}      (nearest parent first)
      meta          : {category_id: {name, path, is_target}}
    """
    if rules_path is None:
        rules_path = find_category_model()
    with open(rules_path) as f:
        rules = json.load(f)

    name_to_id = {n["name"]: n["id"] for n in rules["nodes"]}
    target_labels = {}
    for node in rules["nodes"]:
        if node.get("comparison_target"):
            desc = " ".join((node.get("description") or node["name"]).split())
            target_labels[node["name"]] = desc[:400]
    ancestors = {}
    for n in rules["nodes"]:
        ancestors[n["id"]] = [name_to_id[a] for a in reversed(n.get("path", []))
                              if a in name_to_id]
    meta = {n["id"]: {"name": n["name"], "path": n.get("path", []),
                      "is_target": n.get("comparison_target", False)}
            for n in rules["nodes"]}
    all_ids = [n["id"] for n in rules["nodes"]]
    return target_labels, name_to_id, all_ids, ancestors, meta


def build_prompt(target_labels):
    """Multi-label classification prompt: return ALL applicable categories as a
    JSON array (empty if none). Category definitions steer the choice."""
    lines = ["You are labeling a customer's sentence from a call transcript.",
             "Choose ALL categories that clearly apply (a sentence may match "
             "several, or none). Categories and their definitions:"]
    for name, desc in target_labels.items():
        lines.append("- %s: %s" % (name, desc))
    lines.append("Reply with ONLY a JSON array of the exact category names that "
                 "apply, e.g. [\"Loyalty Rewards - Points\"]. If none apply, "
                 "reply []. Sentence: ")
    return " ".join(lines)


def run():
    from pyspark.sql import SparkSession
    from pyspark.sql import functions as F
    from pyspark.sql.types import ArrayType, StringType

    spark = SparkSession.builder.appName("gm_voc_ai_classify").getOrCreate()
    params = get_params()
    print("Params: %s" % params)

    target_labels, name_to_id, all_ids, ancestors, meta = load_categories()
    target_names = list(target_labels.keys())
    prompt = build_prompt(target_labels)

    limit = int(params.get("sample_limit") or 0)
    limit_clause = ("LIMIT %d" % limit) if limit > 0 else ""

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

    # MULTI-LABEL: ask the LLM (via ai_query) for the JSON array of ALL applicable
    # categories, so a sentence can carry several — matching the rule engine and
    # XM Discover. ai_query returns a STRING; parse it into an array<string>.
    endpoint = params["classify_endpoint"]
    classified = (scoped
        .withColumn("_prompt", F.lit(prompt))
        .withColumn("ai_raw", F.expr(
            "CAST(ai_query('%s', concat(_prompt, words)) AS STRING)" % endpoint))
        .withColumn("ai_categories",
                    F.from_json(F.col("ai_raw"), ArrayType(StringType())))
        .withColumn("sentiment", F.expr("CAST(ai_analyze_sentiment(words) AS STRING)"))
        .drop("_prompt"))

    # Map returned category NAMES -> ids, drop anything unrecognized, then roll up
    # to ancestor categories (a Python UDF keeps the hierarchy logic in one place).
    def expand_ids(cat_names):
        if not cat_names:
            return []
        out = set()
        for nm in cat_names:
            cid = name_to_id.get(nm)
            if not cid:
                continue
            out.add(cid)
            for aid in ancestors.get(cid, []):
                out.add(aid)
        return sorted(out)

    expand_udf = F.udf(expand_ids, ArrayType(StringType()))
    classified = classified.withColumn("_matched_ids", expand_udf(F.col("ai_categories")))

    # One 0/1 column per category (leaves + rolled-up parents), same shape as the
    # rule engine's output so the comparison job is apples-to-apples.
    for cid in all_ids:
        classified = classified.withColumn(
            cid, F.array_contains(F.col("_matched_ids"), cid).cast("int"))

    result = classified.select(
        "natural_id", "id_document", "id_verbatim", "document_date", "words",
        "sentiment",
        F.to_json(F.col("ai_categories")).alias("ai_categories"),
        *all_ids)

    result.write.mode("overwrite").format("delta") \
        .option("overwriteSchema", "true").saveAsTable(params["ai_tags_table"])

    print("Wrote %s" % params["ai_tags_table"])
    # Per-category hit counts (leaves + parents).
    agg = result.agg(*[F.sum(F.col(cid)).alias(cid) for cid in all_ids]).collect()[0]
    for cid in all_ids:
        star = "*" if meta[cid]["is_target"] else " "
        print("%s %-42s %s" % (star, meta[cid]["name"], agg[cid]))


if __name__ == "__main__":
    run()
