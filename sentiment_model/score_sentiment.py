"""
score_sentiment.py
------------------
Batch scoring for the GM VOC POC sentiment track. Loads the registered
transformer sentiment model from Unity Catalog (produced by
train_sentiment_model.py) and scores in-scope verbatims, alongside the VADER
lexicon baseline, so the two methods can be compared per sentence.

Output (Delta): <scored_table> with
  words, transformer_label, transformer_score, vader_label, vader_compound

REQUIREMENTS: Databricks ML runtime; mlflow; the registered model must exist.
If the model isn't registered yet (e.g. training was skipped on synthetic data),
the transformer columns are written NULL and only VADER is populated.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# Standalone/local defaults; the bundle passes full table names as params in
# production (built from bundle variables), overriding these.
CATALOG = "daria_krasavina"
SCHEMA = "gm_voc"
_NS = "%s.%s" % (CATALOG, SCHEMA)

DEFAULTS = {
    "sentence_table": _NS + ".qualtrics_audio_transcripts_sentence_level_sample_data",
    "scored_table": _NS + ".voc_sentiment_scored",
    "registered_model": _NS + ".voc_sentiment_transformer",
    "model_alias": "champion",   # UC alias; falls back to latest version
    "date_start": "2025-07-01",
    "date_end": "2026-06-30",
    "sample_limit": "300",
}


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


def _load_transformer(params):
    """Return an mlflow pyfunc model, or None if not registered yet."""
    import mlflow
    mlflow.set_registry_uri("databricks-uc")
    name = params["registered_model"]
    for uri in ("models:/%s@%s" % (name, params["model_alias"]),
                "models:/%s/latest" % name):
        try:
            return mlflow.pyfunc.load_model(uri)
        except Exception as e:
            print("Could not load %s (%s)" % (uri, e))
    return None


def run():
    from pyspark.sql import SparkSession
    from pyspark.sql import functions as F
    from pyspark.sql.types import (DoubleType, StringType, StructField,
                                    StructType)
    from vader_baseline import VaderScorer

    spark = SparkSession.builder.appName("gm_voc_sentiment_score").getOrCreate()
    params = get_params()
    print("Params: %s" % params)

    limit = int(params.get("sample_limit") or 0)
    limit_clause = ("LIMIT %d" % limit) if limit > 0 else ""
    scoped = spark.sql("""
        SELECT natural_id, id_verbatim, words FROM {sent}
        WHERE lower(language)='english' AND lower(id_source)='audio'
          AND lower(verbatimtype)='clientverbatim'
          AND to_date(document_date) BETWEEN '{ds}' AND '{de}'
          AND words IS NOT NULL AND length(trim(words)) > 2
        {limit}
    """.format(sent=params["sentence_table"], ds=params["date_start"],
               de=params["date_end"], limit=limit_clause))

    # VADER baseline (pure Python, serverless-safe).
    scorer = VaderScorer()
    vschema = StructType([StructField("vader_label", StringType()),
                          StructField("vader_compound", DoubleType())])
    vudf = F.udf(lambda t: scorer.score(t), vschema)
    scored = (scoped
        .withColumn("_v", vudf(F.col("words")))
        .withColumn("vader_label", F.col("_v.vader_label"))
        .withColumn("vader_compound", F.col("_v.vader_compound"))
        .drop("_v"))

    # Transformer, if the registered model exists.
    model = _load_transformer(params)
    if model is not None:
        import pandas as pd

        pdf = scored.select("id_verbatim", "words").toPandas()
        preds = model.predict(pdf["words"].tolist())
        # mlflow transformers pyfunc returns a list of {label, score} dicts or
        # a DataFrame; normalize to two columns.
        labels, scores = [], []
        for p in preds:
            if isinstance(p, dict):
                labels.append(p.get("label"))
                scores.append(float(p.get("score", 0.0)))
            else:
                labels.append(str(p))
                scores.append(None)
        pdf["transformer_label"] = labels
        pdf["transformer_score"] = scores
        tdf = spark.createDataFrame(pdf[["id_verbatim", "transformer_label",
                                         "transformer_score"]])
        scored = scored.join(tdf, on="id_verbatim", how="left")
    else:
        print("No registered transformer model; writing VADER only.")
        scored = (scored
            .withColumn("transformer_label", F.lit(None).cast("string"))
            .withColumn("transformer_score", F.lit(None).cast("double")))

    scored.write.mode("overwrite").format("delta") \
        .option("overwriteSchema", "true").saveAsTable(params["scored_table"])
    print("Wrote %s" % params["scored_table"])
    scored.groupBy("vader_label", "transformer_label").count() \
        .orderBy(F.col("count").desc()).show(50, truncate=False)


if __name__ == "__main__":
    run()
