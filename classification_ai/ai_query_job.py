"""
ai_query_job.py
------------------
Lightweight AI-powered topic classification for the GM VOC POC using Databricks
built-in AI functions — the "AI-assisted classification" track that complements
the deterministic rule engine (voc_topic_model_job.py).

This is a genuinely different approach from the rule engine: instead of
hand-maintained keyword rules, an LLM decides which categories a sentence
belongs to, steered by each category's business definition.

COST / DEDUP:
  ai_query only ever sees the sentence text (`words`), so identical sentences are
  the same classification case. We classify each DISTINCT sentence once, then join
  the tags back to every row that shares that text — same output row count, but you
  pay per unique sentence, not per row (contact-center transcripts repeat heavily).

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
"""
import json
import os
import sys

# Standalone/local defaults; the bundle passes full table names as params in
# production (built from bundle variables), overriding these.
CATALOG = "daria_krasavina"
SCHEMA = "gm_voc"
_NS = "%s.%s" % (CATALOG, SCHEMA)

DEFAULTS = {
    "sentence_table": _NS + ".qualtrics_audio_transcripts_sentence_level_sample_data",
    "metadata_table": _NS + ".qualtrics_audio_transcripts_metadata_sample_data",
    "ai_tags_table": _NS + ".voc_classification_ai_query_tags",
    # AI track runs on a SINGLE calendar day, independent of the rule engine's
    # window. The scope SQL matches to_date(document_date) == classify_date.
    "classify_date": "2026-06-11",
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
    JSON array (empty if none), with strict guidance AGAINST tagging filler.

    Tightened to fix heavy over-tagging observed on real data — short generic
    utterances ("What?", "Huh.", "Get what?") were being labeled
    "Confusing/Makes No Sense". Most sentences should return []."""
    lines = [
        "You label a customer's sentence from a call-center transcript.",
        "Assign a category ONLY when the sentence CLEARLY and EXPLICITLY expresses "
        "it. MOST sentences match nothing — return [] for greetings, small talk, "
        "back-channel, acknowledgments, and generic or short clarifying questions "
        "(e.g. 'What?', 'Huh?', 'Pardon me?', 'Get what?', 'I don't know.', "
        "'Okay.', 'Um.'). Those are NOT categories.",
        "Do NOT tag 'CC Advisor - Confusing/Makes No Sense' merely because the "
        "customer asks a question or sounds unsure — tag it ONLY when the customer "
        "explicitly says the advisor, information, or instructions were confusing "
        "or made no sense.",
        "If surrounding conversation is shown for context, classify ONLY the "
        "sentence marked between >>> and <<<; if no markers are present, classify "
        "the whole text.",
        "Categories and their definitions:",
    ]
    for name, desc in target_labels.items():
        lines.append("- %s: %s" % (name, desc))
    lines.append("Reply with ONLY a JSON array of the exact category names that "
                 "clearly apply, e.g. [\"Loyalty Rewards - Points\"]. If none "
                 "apply, reply []. Text: ")
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
          AND to_date(s.document_date) = '{day}'
          AND s.{text} IS NOT NULL AND length(trim(s.{text})) > 0
        {limit}
    """.format(jk=JOIN_KEY, text=TEXT_FIELD, sent=params["sentence_table"],
               day=params["classify_date"], limit=limit_clause)
    ai_tags_table = params["ai_tags_table"]
    # Serverless compute forbids .persist()/.cache() (NOT_SUPPORTED_WITH_SERVERLESS:
    # PERSIST TABLE), so we materialize to intermediate Delta tables instead. Writing
    # forces evaluation once; reading the table back is a plain scan that does NOT
    # re-run ai_query.
    scoped_tmp = ai_tags_table + "__scoped_tmp"
    bysentence_tmp = ai_tags_table + "__bysentence_tmp"

    # Materialize the in-scope population once. This is the set we WRITE (every row
    # keeps its own natural_id / metadata). Materializing also (a) scans the (large)
    # source table only once, and (b) pins the rows so distinct + join see the same
    # set when a LIMIT sample is used.
    (spark.sql(scoped_sql).write.mode("overwrite").format("delta")
        .option("overwriteSchema", "true").saveAsTable(scoped_tmp))
    scoped = spark.table(scoped_tmp)
    rows_total = scoped.count()

    # DEDUP: the LLM only ever sees `words`, so two rows with identical text are
    # the SAME classification case (no per-row metadata is fed to ai_query).
    # Classify each DISTINCT sentence once, then fan the tags back out to every
    # row that shares that text. This cuts cost (you pay per unique sentence, not
    # per row) and makes duplicate rows consistent (ai_query is non-deterministic,
    # so without dedup identical sentences could otherwise get different tags).
    distinct_sentences = scoped.select("words").distinct()

    # MULTI-LABEL: ask the LLM (via ai_query) for the JSON array of ALL applicable
    # categories, so a sentence can carry several — matching the rule engine and
    # XM Discover. ai_query returns a STRING; parse it into an array<string>.
    endpoint = params["classify_endpoint"]
    tagged_unique = (distinct_sentences
        .withColumn("_prompt", F.lit(prompt))
        .withColumn("ai_raw", F.expr(
            "CAST(ai_query('%s', concat(_prompt, words)) AS STRING)" % endpoint))
        .withColumn("ai_categories",
                    F.from_json(F.col("ai_raw"), ArrayType(StringType())))
        .drop("_prompt", "ai_raw"))

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
    tagged_unique = tagged_unique.withColumn("_matched_ids", expand_udf(F.col("ai_categories")))

    # One 0/1 column per category (leaves + rolled-up parents), same shape as the
    # rule engine's output so the comparison job is apples-to-apples.
    for cid in all_ids:
        tagged_unique = tagged_unique.withColumn(
            cid, F.array_contains(F.col("_matched_ids"), cid).cast("int"))

    # Per-sentence tag table used for the join back (one row per unique sentence).
    tagged_unique = tagged_unique.select(
        "words", F.to_json(F.col("ai_categories")).alias("ai_categories"), *all_ids)

    # Materialize the LLM step EXACTLY ONCE (serverless-safe substitute for
    # persist): writing runs ai_query once; the join below reads this table back
    # instead of re-invoking the model, which is paid + non-deterministic.
    (tagged_unique.write.mode("overwrite").format("delta")
        .option("overwriteSchema", "true").saveAsTable(bysentence_tmp))
    tagged_unique = spark.table(bysentence_tmp)
    unique_count = tagged_unique.count()
    dup_pct = (100.0 * (rows_total - unique_count) / rows_total) if rows_total else 0.0
    print("In-scope rows: %d | unique sentences classified: %d | duplicates skipped: %.1f%%"
          % (rows_total, unique_count, dup_pct))

    # Fan the per-sentence tags back out to ALL in-scope rows (unchanged output
    # shape + row count), so the comparison job still lines up row-for-row.
    result = (scoped.join(tagged_unique, on="words", how="left")
              .select("natural_id", "id_document", "id_verbatim", "document_date",
                      "words", "ai_categories", *all_ids))

    result.write.mode("overwrite").format("delta") \
        .option("overwriteSchema", "true").saveAsTable(ai_tags_table)
    print("Wrote %s" % ai_tags_table)

    # Per-category hit counts (leaves + parents). Read the written table back so the
    # aggregation does not recompute the join.
    written = spark.table(ai_tags_table)
    agg = written.agg(*[F.sum(F.col(cid)).alias(cid) for cid in all_ids]).collect()[0]
    for cid in all_ids:
        star = "*" if meta[cid]["is_target"] else " "
        print("%s %-42s %s" % (star, meta[cid]["name"], agg[cid]))

    # Clean up the intermediate tables (only needed during the run).
    for _t in (bysentence_tmp, scoped_tmp):
        spark.sql("DROP TABLE IF EXISTS %s" % _t)


if __name__ == "__main__":
    run()
