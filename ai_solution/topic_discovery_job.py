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
  3. Cluster the embeddings with Spark MLlib KMeans  -> emergent themes.
  4. Auto-name each cluster with ai_gen over its representative verbatims.

This is genuine ML: sentences are grouped by *semantic similarity*, not
keywords, so paraphrases and novel phrasings that share meaning land together.
The output is a set of discovered themes with sizes, sample verbatims, and
LLM-generated names/summaries — feeding the "global other" review GM does today
by hand.

REQUIREMENTS: serverless compute, DBR 18.2+, Model-Serving-supported region,
Spark MLlib (built in). Embedding calls are pay-per-token — use sample_limit.
"""
import os
import sys

DEFAULTS = {
    "sentence_table": "daria_krasavina.gm_voc.qualtrics_audio_transcripts_sentence_level_sample_data",
    "themes_table": "daria_krasavina.gm_voc.voc_discovered_themes",
    "assignments_table": "daria_krasavina.gm_voc.voc_theme_assignments",
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
    from pyspark.ml.feature import VectorAssembler
    from pyspark.ml.clustering import KMeans
    from pyspark.ml.functions import array_to_vector

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
    embedded = embedded.withColumn("features", array_to_vector("embedding"))
    embedded.cache()

    # 3. Cluster.
    kmeans = KMeans(k=k, seed=42, featuresCol="features", predictionCol="theme_id")
    model = kmeans.fit(embedded)
    assigned = model.transform(embedded).select(
        "natural_id", "id_document", "id_verbatim", "words", "theme_id")
    assigned.write.mode("overwrite").format("delta") \
        .option("overwriteSchema", "true").saveAsTable(params["assignments_table"])

    # 4. For each cluster, pull representative verbatims and auto-name with ai_gen.
    #    Take up to 8 example sentences per theme to keep the prompt small.
    reps = (assigned.groupBy("theme_id")
            .agg(F.count("*").alias("size"),
                 F.slice(F.collect_list("words"), 1, 8).alias("examples")))
    reps = reps.withColumn(
        "examples_text", F.concat_ws("\n- ", F.col("examples")))
    name_prompt = (
        "You are analyzing customer service call transcripts. Below are example "
        "customer sentences from one cluster. Respond with a JSON object with "
        "two keys: \"name\" (a 2-5 word theme label) and \"summary\" (one "
        "sentence describing the theme). Sentences:\n- "
    )
    themes = reps.withColumn(
        "ai_named",
        F.expr("ai_gen(concat('%s', examples_text))" % name_prompt.replace("'", "''")))
    themes = themes.select(
        "theme_id", "size",
        F.expr("try_parse_json(ai_named):name::string").alias("theme_name"),
        F.expr("try_parse_json(ai_named):summary::string").alias("theme_summary"),
        F.col("ai_named").alias("ai_named_raw"),
        "examples")
    themes = themes.orderBy(F.col("size").desc())
    themes.write.mode("overwrite").format("delta") \
        .option("overwriteSchema", "true").saveAsTable(params["themes_table"])

    print("Wrote %s (%d themes) and %s" % (
        params["themes_table"], k, params["assignments_table"]))
    themes.select("theme_id", "size", "theme_name", "theme_summary").show(50, truncate=False)


if __name__ == "__main__":
    run()
