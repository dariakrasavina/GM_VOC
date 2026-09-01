"""
ab_test_prompt.py
-----------------
A/B test for the ai_classify prompt: does the SHORTER prompt label sentences the
same way as the CURRENT one? Run this BEFORE switching the production job to the
trimmed prompt, so you know whether the ~40% token saving costs any accuracy.

WHY THREE PASSES (this is the important part):
  ai_query is non-deterministic — the SAME prompt run twice does not fully agree
  with itself. So we run three passes over the same sample:
    cur   - current prompt (build_prompt from category_model.json)
    trim  - trimmed prompt (TRIMMED_PROMPT below)
    cur2  - current prompt AGAIN
  Then:
    A/B agreement    = how often trim matches cur   (prompt effect + noise)
    noise-floor      = how often cur2 matches cur    (noise alone)
  If A/B agreement ~= noise floor, the trimmed prompt is effectively equivalent
  and it is safe to switch. If A/B is meaningfully below the noise floor, the
  trimmed wording is changing decisions — keep the current prompt.

Compares the SET of categories returned per sentence (order-independent). Samples
DISTINCT sentences (matching the production dedup) for one day.

OUTPUT:
  <detail_table> - one row per sampled sentence: the sentence, each pass's
                   category list, and the two match flags. Printed summary covers
                   overall agreement, the noise floor, and a per-category
                   breakdown, plus a sample of disagreements.

COST: sample_limit distinct sentences x 3 ai_query calls (cheap; keep it small).
Requires serverless compute + DBR 18.2+ + a Model-Serving region.
"""
import os
import sys

# Reuse the exact category loader + current prompt builder the real job uses, so
# "cur" here is byte-identical to production.
from ai_classify_job import build_prompt, load_categories

CATALOG = "daria_krasavina"
SCHEMA = "gm_voc"
_NS = "%s.%s" % (CATALOG, SCHEMA)

DEFAULTS = {
    "sentence_table": _NS + ".qualtrics_audio_transcripts_sentence_level_sample_data",
    "detail_table": _NS + ".voc_ai_prompt_ab_detail",
    "classify_date": "2026-06-11",
    "classify_endpoint": "databricks-meta-llama-3-3-70b-instruct",
    # Distinct sentences to sample. Keep small — this is a diagnostic, not a run.
    "sample_limit": "300",
}

TEXT_FIELD = "words"
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# The candidate trimmed prompt (exactly what would go into build_prompt). It uses
# the SAME category names as the model (required for name->id mapping); only the
# preamble, the definition wording, and the output instruction are shortened.
# If category names change in category_model.json, update this text — run() asserts
# every target name still appears here so a stale prompt fails loudly.
TRIMMED_PROMPT = (
    "Label this customer call-transcript sentence. Return a JSON array of ALL "
    "category names that clearly apply ([] if none). Categories:\n"
    "- CC Advisor - Confusing/Makes No Sense: customer confused / something "
    "doesn't make sense (exclude dealer, Roadside)\n"
    "- CC Advisor - Inaccurate Information: customer given inaccurate info/"
    "directions, or advisor entered wrong info (exclude dealer, Roadside)\n"
    "- Loyalty Rewards - Points: explicit mention of GM My Rewards points\n"
    "- Points - Redeem: spending/redeeming points (e.g. for service allowance)\n"
    'Example: ["Loyalty Rewards - Points"]. Sentence: '
)


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
    from pyspark.sql.types import ArrayType, StringType

    spark = SparkSession.builder.appName("gm_voc_prompt_ab").getOrCreate()
    params = get_params()
    print("Params: %s" % params)

    target_labels, name_to_id, all_ids, ancestors, meta = load_categories()
    prompt_cur = build_prompt(target_labels)

    # Fail loudly if the trimmed prompt has drifted from the model's category names
    # (otherwise those categories could never be returned and the A/B would be a lie).
    for nm in target_labels:
        if nm not in TRIMMED_PROMPT:
            raise ValueError(
                "TRIMMED_PROMPT is missing category name %r — update the prompt text "
                "in ab_test_prompt.py before running the A/B." % nm)

    n = int(params.get("sample_limit") or 300)
    endpoint = params["classify_endpoint"]
    day = params["classify_date"]

    # Sample DISTINCT in-scope sentences for the day (dedup mirrors production).
    # rand() with a fixed seed makes the sample reproducible across passes/runs.
    sample_sql = """
        SELECT words FROM (
          SELECT DISTINCT {text} AS words
          FROM {sent}
          WHERE lower(language) = 'english'
            AND lower(id_source) = 'audio'
            AND lower(verbatimtype) = 'clientverbatim'
            AND to_date(document_date) = '{day}'
            AND {text} IS NOT NULL AND length(trim({text})) > 0
        ) ORDER BY rand(42) LIMIT {n}
    """.format(text=TEXT_FIELD, sent=params["sentence_table"], day=day, n=n)
    sample = spark.sql(sample_sql)

    def add_pass(df, prompt, prefix):
        """Add <prefix> = parsed category-name array from one ai_query pass."""
        return (df
            .withColumn("_p", F.lit(prompt))
            .withColumn("_raw", F.expr(
                "CAST(ai_query('%s', concat(_p, words)) AS STRING)" % endpoint))
            .withColumn(prefix, F.from_json(F.col("_raw"), ArrayType(StringType())))
            .drop("_p", "_raw"))

    res = add_pass(sample, prompt_cur, "cur")
    res = add_pass(res, TRIMMED_PROMPT, "trim")
    res = add_pass(res, prompt_cur, "cur2")

    # Normalize each pass to a sorted set of recognized category IDs, so agreement
    # is order-independent and ignores anything the LLM returned that is not a real
    # category name.
    def to_idset(names):
        if not names:
            return []
        return sorted({name_to_id[nm] for nm in names if nm in name_to_id})

    idset_udf = F.udf(to_idset, ArrayType(StringType()))
    for p in ("cur", "trim", "cur2"):
        res = res.withColumn(p + "_ids", idset_udf(F.col(p)))

    # Exact set match (both id lists are sorted, so == compares the sets).
    res = res.withColumn("ab_match", (F.col("trim_ids") == F.col("cur_ids")).cast("int"))
    res = res.withColumn("noise_match", (F.col("cur2_ids") == F.col("cur_ids")).cast("int"))

    # Materialize once — ai_query is paid + non-deterministic; downstream actions
    # must not re-trigger it.
    res = res.persist()
    total = res.count()
    if total == 0:
        print("No in-scope sentences sampled for %s; nothing to compare." % day)
        return

    sums = res.agg(F.sum("ab_match").alias("ab"),
                   F.sum("noise_match").alias("noise")).collect()[0]
    ab = int(sums["ab"] or 0)
    noise = int(sums["noise"] or 0)

    print("\n================ ai_classify prompt A/B ================")
    print("day               : %s" % day)
    print("sentences sampled : %d (distinct)" % total)
    print("A/B agreement     : %d/%d = %.1f%%  (trimmed vs current)"
          % (ab, total, 100.0 * ab / total))
    print("noise floor       : %d/%d = %.1f%%  (current vs current, 2nd run)"
          % (noise, total, 100.0 * noise / total))
    gap = (100.0 * noise / total) - (100.0 * ab / total)
    print("gap (noise - A/B) : %.1f pts  -> ~0 means the trimmed prompt is "
          "equivalent; a large positive gap means it changes decisions" % gap)

    # Per-category positives + disagreement direction (target categories only).
    print("\nPer-category (target categories):")
    print("  %-42s %6s %6s %6s %8s %8s" %
          ("category", "cur", "trim", "both", "cur_only", "trim_only"))
    for cid in all_ids:
        if not meta[cid]["is_target"]:
            continue
        cname = meta[cid]["name"]
        a = res.agg(
            F.sum(F.array_contains("cur_ids", cid).cast("int")).alias("cur"),
            F.sum(F.array_contains("trim_ids", cid).cast("int")).alias("trim"),
            F.sum((F.array_contains("cur_ids", cid) & F.array_contains("trim_ids", cid)).cast("int")).alias("both"),
            F.sum((F.array_contains("cur_ids", cid) & ~F.array_contains("trim_ids", cid)).cast("int")).alias("cur_only"),
            F.sum((~F.array_contains("cur_ids", cid) & F.array_contains("trim_ids", cid)).cast("int")).alias("trim_only"),
        ).collect()[0]
        print("  %-42s %6d %6d %6d %8d %8d" %
              (cname, int(a["cur"] or 0), int(a["trim"] or 0), int(a["both"] or 0),
               int(a["cur_only"] or 0), int(a["trim_only"] or 0)))

    # Persist the per-sentence detail for inspection.
    detail = res.select(
        "words",
        F.to_json("cur_ids").alias("current_prompt_categories"),
        F.to_json("trim_ids").alias("trimmed_prompt_categories"),
        F.to_json("cur2_ids").alias("current_prompt_categories_run2"),
        "ab_match", "noise_match")
    (detail.write.mode("overwrite").format("delta")
        .option("overwriteSchema", "true").saveAsTable(params["detail_table"]))
    print("\nWrote per-sentence detail to %s" % params["detail_table"])

    print("\nSample disagreements (trimmed != current):")
    (res.filter(F.col("ab_match") == 0)
        .select("words", "cur_ids", "trim_ids").show(20, truncate=False))


if __name__ == "__main__":
    run()
