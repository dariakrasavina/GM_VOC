"""
compare_approaches_job.py
-------------------------
Regression / agreement analysis between the two classification tracks:
  A) rule engine   -> voc_classification_rule_tags        (deterministic XM Discover replica)
  B) AI classifier -> voc_classification_ai_tags      (LLM via ai_classify)

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

# Standalone/local defaults; the bundle passes full table names as params in
# production (built from bundle variables), overriding these.
CATALOG = "daria_krasavina"
SCHEMA = "gm_voc"
_NS = "%s.%s" % (CATALOG, SCHEMA)

DEFAULTS = {
    "rule_tags_table": _NS + ".voc_classification_rule_tags",
    "ai_tags_table": _NS + ".voc_classification_ai_tags",
    "comparison_table": _NS + ".voc_classification_comparison",
}
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


def all_categories():
    """[(category_id, name, is_target) ...] in model order."""
    with open(find_category_model()) as f:
        rules = json.load(f)
    return [(n["id"], n["name"], n.get("comparison_target", False))
            for n in rules["nodes"]]


def run():
    from pyspark.sql import SparkSession, Row
    from pyspark.sql import functions as F

    spark = SparkSession.builder.appName("gm_voc_compare").getOrCreate()
    params = get_params()
    print("Params: %s" % params)

    rules = spark.table(params["rule_tags_table"])
    ai = spark.table(params["ai_tags_table"])

    # Both tables now carry one 0/1 column per category (leaves + rolled-up
    # parents), so we can compare like-for-like per category on the shared
    # sentences (join on id_verbatim). Only categories present in BOTH tables
    # are compared.
    cats = [(cid, name, tgt) for (cid, name, tgt) in all_categories()
            if cid in rules.columns and cid in ai.columns]

    rows = []
    for cid, name, is_target in cats:
        r = rules.select("id_verbatim", F.col(cid).alias("rule_pos"))
        a = ai.select("id_verbatim", F.col(cid).alias("ai_pos"))
        joined = r.join(a, on="id_verbatim", how="inner")

        agg = joined.agg(
            F.count("*").alias("n"),
            F.sum("rule_pos").alias("rule_pos"),
            F.sum("ai_pos").alias("ai_pos"),
            F.sum((F.col("rule_pos") == 1) & (F.col("ai_pos") == 1)).cast("int").alias("both"),
            F.sum((F.col("rule_pos") == 1) & (F.col("ai_pos") == 0)).cast("int").alias("rule_only"),
            F.sum((F.col("rule_pos") == 0) & (F.col("ai_pos") == 1)).cast("int").alias("ai_only"),
        ).collect()[0]

        rule_positives = int(agg["rule_pos"] or 0)
        ai_positives = int(agg["ai_pos"] or 0)
        both = int(agg["both"] or 0)
        # AI metrics using the rule engine as (proxy) ground truth.
        precision = (both / ai_positives) if ai_positives else None
        recall = (both / rule_positives) if rule_positives else None
        f1 = (2 * precision * recall / (precision + recall)
              if precision and recall else None)

        rows.append(Row(
            category_id=cid, category=name, is_target=bool(is_target),
            compared_sentences=int(agg["n"] or 0),
            rule_positives=rule_positives, ai_positives=ai_positives,
            agree_positive=both, rule_only=int(agg["rule_only"] or 0),
            ai_only=int(agg["ai_only"] or 0),
            ai_precision_vs_rules=precision, ai_recall_vs_rules=recall,
            ai_f1_vs_rules=f1))

    comp = spark.createDataFrame(rows)
    comp.write.mode("overwrite").format("delta") \
        .option("overwriteSchema", "true").saveAsTable(params["comparison_table"])
    print("Wrote %s" % params["comparison_table"])
    comp.show(truncate=False)


if __name__ == "__main__":
    run()
