"""
vader_baseline.py
-----------------
A lexicon-based sentiment baseline for the GM VOC POC — the pre-transformer
"lexicon dictionary" era that Qualtrics describes as their OLD sentiment system
(before moving to a transformer). It gives the trained transformer
(train_sentiment_model.py) a fair, cheap, fully-explainable comparison point.

Uses VADER (Valence Aware Dictionary and sEntiment Reasoner), a rule/lexicon
sentiment method well suited to short, informal, conversational text. VADER
returns a compound score in [-1, 1]; we bucket it into the same 5-class scheme
as the transformer so the two are directly comparable.

Pure Python + the `vaderSentiment` package (tiny, pip-installable, no GPU),
so unlike the transformer this runs and is verifiable in a plain environment.
Falls back to a small built-in lexicon if vaderSentiment is unavailable.
"""

# Same 5-class scheme as the transformer track.
LABELS = ["Very Negative", "Negative", "Neutral", "Positive", "Very Positive"]


def _bucket(compound):
    """Map a VADER compound score in [-1, 1] to the 5-class label."""
    if compound <= -0.6:
        return "Very Negative"
    if compound <= -0.05:
        return "Negative"
    if compound < 0.05:
        return "Neutral"
    if compound < 0.6:
        return "Positive"
    return "Very Positive"


# --- minimal fallback lexicon (used only if vaderSentiment isn't installed) ---
_FALLBACK_POS = {"good", "great", "excellent", "happy", "love", "helpful",
                 "thanks", "thank", "perfect", "awesome", "appreciate", "nice"}
_FALLBACK_NEG = {"bad", "terrible", "awful", "angry", "hate", "confused",
                 "wrong", "unhappy", "frustrated", "useless", "worst", "poor"}


def _fallback_score(text):
    toks = [t.strip(".,!?").lower() for t in (text or "").split()]
    pos = sum(1 for t in toks if t in _FALLBACK_POS)
    neg = sum(1 for t in toks if t in _FALLBACK_NEG)
    total = pos + neg
    if total == 0:
        return 0.0
    return (pos - neg) / float(total)


class VaderScorer(object):
    """Callable scorer: text -> (label, compound_score)."""

    def __init__(self):
        self._analyzer = None
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
            self._analyzer = SentimentIntensityAnalyzer()
            self.method = "vader"
        except Exception:
            self.method = "fallback_lexicon"

    def score(self, text):
        if self._analyzer is not None:
            compound = self._analyzer.polarity_scores(text or "")["compound"]
        else:
            compound = _fallback_score(text)
        return _bucket(compound), compound


def score_dataframe(spark, sentence_table, out_table,
                    date_start="2025-07-01", date_end="2026-06-30",
                    sample_limit=0):
    """Spark entry: score in-scope verbatims with VADER, write a Delta table.

    The scorer runs per-row via a UDF; it's pure-Python so it is serverless-safe
    (no persistence) and needs no GPU.
    """
    from pyspark.sql import functions as F
    from pyspark.sql.types import StringType, StructField, StructType, DoubleType

    limit_clause = ("LIMIT %d" % sample_limit) if sample_limit and sample_limit > 0 else ""
    scoped = spark.sql("""
        SELECT natural_id, id_verbatim, words FROM {sent}
        WHERE lower(language)='english' AND lower(id_source)='audio'
          AND lower(verbatimtype)='clientverbatim'
          AND to_date(document_date) BETWEEN '{ds}' AND '{de}'
          AND words IS NOT NULL AND length(trim(words)) > 2
        {limit}
    """.format(sent=sentence_table, ds=date_start, de=date_end, limit=limit_clause))

    scorer = VaderScorer()
    schema = StructType([
        StructField("vader_label", StringType()),
        StructField("vader_compound", DoubleType()),
    ])

    def _udf(text):
        label, comp = scorer.score(text)
        return (label, float(comp))

    score_udf = F.udf(_udf, schema)
    scored = (scoped
        .withColumn("_s", score_udf(F.col("words")))
        .select("natural_id", "id_verbatim", "words",
                F.col("_s.vader_label").alias("vader_label"),
                F.col("_s.vader_compound").alias("vader_compound")))
    scored.write.mode("overwrite").format("delta") \
        .option("overwriteSchema", "true").saveAsTable(out_table)
    return scored


# Local smoke test: python3 sentiment_model/vader_baseline.py
if __name__ == "__main__":
    scorer = VaderScorer()
    print("method: %s" % scorer.method)
    samples = [
        "I am so happy with the help I received, thank you so much!",
        "This is absolutely terrible, the worst service ever.",
        "The advisor updated my account.",
        "I'm a little confused about the points.",
        "Everything was perfect and the team was wonderful and kind.",
    ]
    for s in samples:
        label, comp = scorer.score(s)
        print("%-14s (%.3f)  %s" % (label, comp, s))
