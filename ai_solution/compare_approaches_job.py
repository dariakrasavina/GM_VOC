"""
compare_approaches_job.py
-------------------------
Regression / agreement analysis between the two classification tracks:
  A) rule engine   -> voc_topic_tags        (deterministic XM Discover replica)
  B) AI classifier -> voc_ai_topic_tags      (LLM via ai_classify)

This is the POC's "show regression results against the current solution"
deliverable, reframed for the AI track: it quantifies where the ML approach
agrees with the rule-based control and surfaces the disagreements for SME review.

Per POC topic it reports, at the sentence grain (joined on id_verbatim):
  - rule_positives  : sentences the rule engine tagged for the topic
  - ai_positives    : sentences ai_classify assigned to the topic
  - agree_positive  : both agree the topic applies
  - rule_only       : rule tagged, AI did not  (candidate AI misses)
  - ai_only         : AI assigned, rule did not (candidate rule-gaps or AI noise)
  - precision/recall of AI *treating the rule engine as ground truth*
    (a proxy only; the true control is GM's XM Discover output)

OUTPUT (Delta): <comparison_table> — one row per POC topic.
"""
import json
import os
import sys

DEFAULTS = {
    "rule_tags_table": "daria_krasavina.gm_voc.voc_topic_tags",
    "ai_tags_table": "daria_krasavina.gm_voc.voc_ai_topic_tags",
    "comparison_table": "daria_krasavina.gm_voc.voc_approach_comparison",
}
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


def target_topics():
    """(rule_column_id, ai_label_name) for each POC comparison topic."""
    with open(find_rules_json()) as f:
        rules = json.load(f)
    out = []
    for n in rules["nodes"]:
        if n.get("comparison_target"):
            out.append((n["id"], n["name"]))
    return out


def run():
    from pyspark.sql import SparkSession, Row
    from pyspark.sql import functions as F

    spark = SparkSession.builder.appName("gm_voc_compare").getOrCreate()
    params = get_params()
    print("Params: %s" % params)

    rules = spark.table(params["rule_tags_table"])
    ai = spark.table(params["ai_tags_table"]).select(
        "id_verbatim", F.col("ai_topic"))

    rows = []
    for rule_col, ai_label in target_topics():
        # Rule-side positives for this topic (column is 0/1).
        r = rules.select("id_verbatim", F.col(rule_col).alias("rule_pos"))
        joined = r.join(ai, on="id_verbatim", how="inner")
        joined = joined.withColumn(
            "ai_pos", (F.col("ai_topic") == F.lit(ai_label)).cast("int"))

        agg = joined.agg(
            F.sum("rule_pos").alias("rule_positives"),
            F.sum("ai_pos").alias("ai_positives"),
            F.sum((F.col("rule_pos") == 1) & (F.col("ai_pos") == 1)).cast("int").alias("_x"),
            F.count("*").alias("n"),
        ).collect()[0]

        # Recompute agreement counts explicitly (booleans -> ints).
        both = joined.filter((F.col("rule_pos") == 1) & (F.col("ai_pos") == 1)).count()
        rule_only = joined.filter((F.col("rule_pos") == 1) & (F.col("ai_pos") == 0)).count()
        ai_only = joined.filter((F.col("rule_pos") == 0) & (F.col("ai_pos") == 1)).count()

        rule_positives = int(agg["rule_positives"] or 0)
        ai_positives = int(agg["ai_positives"] or 0)
        # AI metrics using the rule engine as (proxy) ground truth.
        precision = (both / ai_positives) if ai_positives else None
        recall = (both / rule_positives) if rule_positives else None
        f1 = (2 * precision * recall / (precision + recall)
              if precision and recall else None)

        rows.append(Row(
            topic_id=rule_col, ai_label=ai_label,
            compared_sentences=int(agg["n"] or 0),
            rule_positives=rule_positives, ai_positives=ai_positives,
            agree_positive=both, rule_only=rule_only, ai_only=ai_only,
            ai_precision_vs_rules=precision, ai_recall_vs_rules=recall,
            ai_f1_vs_rules=f1))

    comp = spark.createDataFrame(rows)
    comp.write.mode("overwrite").format("delta") \
        .option("overwriteSchema", "true").saveAsTable(params["comparison_table"])
    print("Wrote %s" % params["comparison_table"])
    comp.show(truncate=False)


if __name__ == "__main__":
    run()
