"""
topic_discovery_job.py
----------------------
Unsupervised NLP topic DISCOVERY for the GM VOC POC — the "find themes we did
not know to look for" capability the scoping document calls for, and the part a
pure rule engine fundamentally cannot do.

Pipeline (all on Databricks):
  1. Scope to in-scope customer verbatims (English + audio + customer-side).
  2. Embed each sentence with a Databricks foundation embedding model via
     ai_query('databricks-gte-large-en', ...)  -> dense vector.
  3. Cluster the embeddings with KMeans (scikit-learn on the driver over the
     bounded sample) -> emergent themes.
  4. Auto-name each cluster with ai_gen over its representative verbatims.

This is genuine ML: sentences are grouped by *semantic similarity*, not
keywords, so paraphrases and novel phrasings that share meaning land together.
The output is a set of discovered themes with sizes, sample verbatims, and
LLM-generated names/summaries — feeding the "global other" review GM does today
by hand.

CLUSTERING NOTE: serverless compute forbids RDD/DataFrame persistence, which
Spark MLlib KMeans needs internally. Since discovery runs on a bounded sample
(sample_limit), we collect embeddings to the driver and cluster with
scikit-learn. For a very large corpus on classic (non-serverless) compute, swap
the sklearn block for pyspark.ml.clustering.KMeans.

REQUIREMENTS: serverless compute, DBR 18.2+, Model-Serving-supported region,
numpy + scikit-learn (bundled in Databricks runtimes). Embedding calls are
pay-per-token — use sample_limit.
"""
import os
import sys

# Standalone/local defaults; the bundle passes full table names as params in
# production (built from bundle variables), overriding these.
CATALOG = "daria_krasavina"
SCHEMA = "gm_voc"
_NS = "%s.%s" % (CATALOG, SCHEMA)

DEFAULTS = {
    "sentence_table": _NS + ".qualtrics_audio_transcripts_sentence_level_sample_data",
    "themes_table": _NS + ".voc_topicmodeling_themes",
    "assignments_table": _NS + ".voc_topicmodeling_assignments",
    "date_start": "2025-07-01",
    "date_end": "2026-06-30",
    "embedding_endpoint": "databricks-gte-large-en",
    "gen_endpoint": "databricks-meta-llama-3-3-70b-instruct",
    "num_clusters": "12",
    "sample_limit": "3000",   # keep embedding cost bounded for the POC
}

TEXT_FIELD = "words"
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


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
    from pyspark.sql import SparkSession
    from pyspark.sql import functions as F

    spark = SparkSession.builder.appName("gm_voc_topic_discovery").getOrCreate()
    params = get_params()
    print("Params: %s" % params)

    k = int(params["num_clusters"])
    limit = int(params.get("sample_limit") or 0)
    limit_clause = ("LIMIT %d" % limit) if limit > 0 else ""

    # 1. Scope.
    scoped_sql = """
        SELECT natural_id, id_document, id_verbatim, {text} AS words
        FROM {sent}
        WHERE lower(language) = 'english'
          AND lower(id_source) = 'audio'
          AND lower(verbatimtype) = 'clientverbatim'
          AND to_date(document_date) BETWEEN '{ds}' AND '{de}'
          AND {text} IS NOT NULL AND length(trim({text})) > 2
        {limit}
    """.format(text=TEXT_FIELD, sent=params["sentence_table"],
               ds=params["date_start"], de=params["date_end"], limit=limit_clause)
    scoped = spark.sql(scoped_sql)

    # 2. Embed. ai_query returns the embedding as an array<double>. If a given
    #    runtime returns the OpenAI-style JSON envelope instead, uncomment the
    #    from_json parse path below.
    embedded = scoped.withColumn(
        "embedding",
        F.expr("ai_query('%s', words)" % params["embedding_endpoint"]))
    # Defensive: if the endpoint returns a JSON envelope string, parse it.
    # embedded = scoped.withColumn("raw", F.expr("ai_query(...)")) \
    #     .withColumn("embedding",
    #                 F.expr("from_json(raw, 'struct<data:array<struct<embedding:array<double>>>>').data[0].embedding"))

    embedded = embedded.filter(F.col("embedding").isNotNull())

    # 3. Cluster. Serverless compute forbids RDD/DataFrame persistence, which
    #    Spark MLlib KMeans requires internally (NOT_SUPPORTED_WITH_SERVERLESS:
    #    PERSIST TABLE). Because discovery runs on a bounded sample (sample_limit),
    #    we collect the embeddings to the driver and cluster with scikit-learn —
    #    serverless-safe and identical output shape. For very large corpora on
    #    classic (non-serverless) compute, swap this for pyspark.ml KMeans.
    import numpy as np
    from sklearn.cluster import KMeans as SKKMeans

    rows = embedded.select("natural_id", "id_document", "id_verbatim",
                           "words", "embedding").collect()
    if not rows:
        print("No embedded rows; nothing to cluster.")
        return
    X = np.array([r["embedding"] for r in rows], dtype="float64")
    k_eff = min(k, len(rows))  # can't have more clusters than points
    labels = SKKMeans(n_clusters=k_eff, random_state=42, n_init=10).fit_predict(X)

    assigned_rows = [
        (r["natural_id"], r["id_document"], r["id_verbatim"], r["words"],
         int(labels[i]))
        for i, r in enumerate(rows)]
    assigned = spark.createDataFrame(
        assigned_rows,
        ["natural_id", "id_document", "id_verbatim", "words", "theme_id"])
    assigned.write.mode("overwrite").format("delta") \
        .option("overwriteSchema", "true").saveAsTable(params["assignments_table"])

    # 4. For each cluster, pull representative verbatims and auto-name with ai_gen.
    #    Take up to 8 example sentences per theme to keep the prompt small.
    reps = (assigned.groupBy("theme_id")
            .agg(F.count("*").alias("size"),
                 F.slice(F.collect_list("words"), 1, 8).alias("examples")))
    reps = reps.withColumn(
        "examples_text", F.concat_ws("; ", F.col("examples")))
    # Ask for a rigid two-line format instead of JSON. JSON from ai_gen was
    # fragile — markdown fences, embedded quotes/apostrophes in the summary, and
    # trailing commas all broke try_parse_json and left names null. A plain
    # "NAME: ...\nSUMMARY: ..." format parses with simple line regex and is
    # immune to those issues.
    name_prompt = (
        "You are analyzing customer service call transcripts. Below are example "
        "customer sentences from one cluster. Reply with EXACTLY two lines and "
        "nothing else:\n"
        "NAME: a 2 to 5 word theme label\n"
        "SUMMARY: one sentence describing the theme\n"
        "Sentences: ")
    themes = (reps
        .withColumn("_prompt", F.lit(name_prompt))
        .withColumn("ai_named", F.expr("ai_gen(concat(_prompt, examples_text))"))
        .drop("_prompt"))
    # Line-based extraction: tolerant of surrounding prose, code fences, quotes.
    themes = themes.select(
        "theme_id", "size",
        F.regexp_extract(F.col("ai_named"), r"(?im)^\s*NAME:\s*(.+?)\s*$", 1).alias("theme_name"),
        F.regexp_extract(F.col("ai_named"), r"(?im)^\s*SUMMARY:\s*(.+?)\s*$", 1).alias("theme_summary"),
        F.col("ai_named").alias("ai_named_raw"),
        "examples")
    # Empty-string extractions (no match) -> NULL for cleaner reporting.
    themes = themes.withColumn(
        "theme_name", F.when(F.length("theme_name") > 0, F.col("theme_name")))
    themes = themes.withColumn(
        "theme_summary", F.when(F.length("theme_summary") > 0, F.col("theme_summary")))
    themes = themes.orderBy(F.col("size").desc())
    themes.write.mode("overwrite").format("delta") \
        .option("overwriteSchema", "true").saveAsTable(params["themes_table"])

    print("Wrote %s (%d themes) and %s" % (
        params["themes_table"], k, params["assignments_table"]))
    themes.select("theme_id", "size", "theme_name", "theme_summary").show(50, truncate=False)


if __name__ == "__main__":
    run()
