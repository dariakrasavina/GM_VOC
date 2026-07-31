"""
voc_topic_model_job.py
----------------------
Databricks / PySpark production job that replicates GM's Qualtrics XM Discover
topic tagging over contact-center audio transcripts, at scale.

Run as a Databricks notebook or as a job task (spark-submit). It imports the
dependency-free tagging core (`tagger.py` + `rule_engine.py`) and applies it via
a vectorized `pandas_udf`, so the logic is byte-identical to what run_local.py
validates locally.

INPUT  (Unity Catalog tables):
  marketing_prod.silver_voice_of_customer_gbl.qualtrics_audio_transcripts_sentence_level
  marketing_prod.silver_voice_of_customer_gbl.qualtrics_audio_metadata
JOIN:  sentence.natural_id == metadata.natural_id
SCOPE: DBX POC global filter (English, audio, customer-side, non-boilerplate)
       + document_date in [2025-07-01, 2026-06-30]

OUTPUT (Delta):
  <output_catalog>.<output_schema>.voc_topic_tags            (per-sentence tags)
  <output_catalog>.<output_schema>.voc_topic_frequencies     (aggregates)

To make the engine importable on executors, ship rule_engine.py, tagger.py and
rules.json with the job (e.g. --py-files, a wheel, or a Repos path on sys.path).
"""
import json
import os
import sys

# --- defaults (override via job params / widgets / --key=value argv) ----------
DEFAULTS = {
    "sentence_table": "daria_krasavina.gm_voc.qualtrics_audio_transcripts_sentence_level_sample_data",
    "metadata_table": "daria_krasavina.gm_voc.qualtrics_audio_transcripts_metadata_sample_data",
    "tags_table": "daria_krasavina.gm_voc.voc_topic_tags",
    "freq_table": "daria_krasavina.gm_voc.voc_topic_frequencies",
    "date_start": "2025-07-01",
    "date_end": "2026-06-30",
}

JOIN_KEY = "natural_id"
TEXT_FIELD = "words"
# Metadata attribute columns referenced by the rule set.
META_ATTRS = ["call_direction", "cc_lob_mv"]
# Sentence attribute columns used by scope filter / rules.
SENT_ATTRS = ["id_source", "verbatimtype", "language"]

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def get_params():
    """Resolve config from (in priority order) Databricks widgets, then
    --key=value argv, then DEFAULTS. Works as a notebook task or spark-submit."""
    params = dict(DEFAULTS)
    # Databricks widgets, if available.
    try:
        from pyspark.dbutils import DBUtils  # noqa
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
    # argv overrides (--key=value).
    for arg in sys.argv[1:]:
        if arg.startswith("--") and "=" in arg:
            k, v = arg[2:].split("=", 1)
            if k in params:
                params[k] = v
    return params


def build_spark():
    from pyspark.sql import SparkSession
    return SparkSession.builder.appName("gm_voc_topic_model").getOrCreate()


def load_and_join(spark, params):
    from pyspark.sql import functions as F

    sent = spark.table(params["sentence_table"])
    meta = spark.table(params["metadata_table"])

    # Only keep metadata columns we actually need, aliased to avoid clashes.
    meta_cols = [JOIN_KEY] + [c for c in META_ATTRS if c in meta.columns]
    meta = meta.select(*meta_cols).dropDuplicates([JOIN_KEY])

    df = sent.join(meta, on=JOIN_KEY, how="left")

    # Date-range scoping on document_date (string ISO timestamp -> date).
    if "document_date" in df.columns:
        d = F.to_date(F.col("document_date"))
        df = df.filter((d >= F.lit(params["date_start"])) & (d <= F.lit(params["date_end"])))
    return df


def make_tag_udf():
    """Vectorized pandas_udf that tags a partition of sentences.

    Returns a JSON string per row: {"in_scope": bool, "topics": {id: [terms]}}.
    The heavy objects (compiled rules) are built once per executor process.
    """
    import pandas as pd
    from pyspark.sql.functions import pandas_udf
    from pyspark.sql.types import StringType

    # Broadcast-friendly: compile once, lazily, per worker.
    _state = {}

    def _tagger():
        if "t" not in _state:
            from tagger import build_tagger, load_rules
            _state["t"] = build_tagger(load_rules())
        return _state["t"]

    attr_cols = SENT_ATTRS + META_ATTRS

    # Pass the text + attribute columns as a single struct so the pandas_udf has
    # exactly one fully type-hinted parameter (Spark rejects untyped/variadic
    # args). The struct arrives as a DataFrame with columns [words, *attr_cols].
    @pandas_udf(StringType())
    def tag_udf(payload: pd.DataFrame) -> pd.Series:
        tg = _tagger()
        out = []
        for _, row in payload.iterrows():
            text = row["words"] or ""
            attrs = {c: row[c] for c in attr_cols}
            in_scope, _ = tg.in_scope(text, attrs)
            topics = tg.tag(text, attrs) if in_scope else {}
            out.append(json.dumps({
                "in_scope": bool(in_scope),
                "topics": {tid: v["terms"] for tid, v in topics.items()},
            }))
        return pd.Series(out)

    return tag_udf, attr_cols


def run():
    from pyspark.sql import functions as F
    from pyspark.sql.types import (ArrayType, BooleanType, MapType, StringType,
                                    StructField, StructType)

    from tagger import build_tagger, load_rules

    spark = build_spark()
    params = get_params()
    print("Params: %s" % params)
    tags_table = params["tags_table"]
    freq_table = params["freq_table"]

    tagger = build_tagger(load_rules())
    topic_ids = tagger.topic_ids()
    topic_meta = tagger.topic_meta()

    df = load_and_join(spark, params)
    tag_udf, attr_cols = make_tag_udf()

    # Build the struct payload: words + each attribute column (aliased so the
    # UDF's DataFrame has stable column names). Missing metadata cols -> null.
    payload_cols = [F.col(TEXT_FIELD).alias("words")]
    for c in attr_cols:
        payload_cols.append(
            (F.col(c) if c in df.columns else F.lit(None).cast("string")).alias(c))
    tagged = df.withColumn("_tag_json", tag_udf(F.struct(*payload_cols)))

    result_schema = StructType([
        StructField("in_scope", BooleanType()),
        StructField("topics", MapType(StringType(), ArrayType(StringType()))),
    ])
    tagged = tagged.withColumn("_tags", F.from_json("_tag_json", result_schema))
    tagged = tagged.withColumn("in_scope", F.col("_tags.in_scope"))

    # Explode each topic into its own boolean column + matched-terms column,
    # mirroring how XM Discover stores one tag attribute per sentence.
    for tid in topic_ids:
        tagged = tagged.withColumn(
            tid, F.when(F.col("_tags.topics").getItem(tid).isNotNull(), F.lit(1)).otherwise(F.lit(0)))
        tagged = tagged.withColumn(
            tid + "__terms",
            F.concat_ws("; ", F.coalesce(F.col("_tags.topics").getItem(tid),
                                         F.array().cast("array<string>"))))

    keep = [JOIN_KEY, "id_document", "id_verbatim", "document_date", "language",
            "verbatimtype", "id_source", TEXT_FIELD, "in_scope"]
    keep = [c for c in keep if c in tagged.columns]
    tagged_out = tagged.select(*keep, *topic_ids, *[t + "__terms" for t in topic_ids])

    (tagged_out.write.mode("overwrite").format("delta")
        .option("overwriteSchema", "true").saveAsTable(tags_table))

    # Aggregate frequency view (sentence + document counts per topic).
    agg_exprs = []
    for tid in topic_ids:
        agg_exprs.append(F.sum(F.col(tid)).alias(tid + "_sentences"))
        agg_exprs.append(
            F.countDistinct(F.when(F.col(tid) == 1, F.col("id_document"))).alias(tid + "_documents"))
    totals = tagged_out.agg(
        F.count(F.lit(1)).alias("sentences_total"),
        F.sum(F.col("in_scope").cast("int")).alias("sentences_in_scope"),
        *agg_exprs).collect()[0].asDict()

    freq_rows = []
    for tid in topic_ids:
        freq_rows.append((
            tid, topic_meta[tid]["name"], " > ".join(topic_meta[tid]["path"]),
            bool(topic_meta[tid]["is_target"]),
            int(totals[tid + "_sentences"] or 0),
            int(totals[tid + "_documents"] or 0),
        ))
    freq_df = spark.createDataFrame(
        freq_rows,
        ["topic_id", "topic", "path", "is_comparison_target",
         "sentences_tagged", "documents_tagged"])
    (freq_df.write.mode("overwrite").format("delta")
        .option("overwriteSchema", "true").saveAsTable(freq_table))

    print("Wrote %s and %s" % (tags_table, freq_table))
    print("Totals: %s" % {k: totals[k] for k in ("sentences_total", "sentences_in_scope")})
    freq_df.orderBy(F.col("sentences_tagged").desc()).show(truncate=False)


if __name__ == "__main__":
    run()
