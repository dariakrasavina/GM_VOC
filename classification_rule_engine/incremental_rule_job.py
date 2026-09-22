"""
incremental_rule_job.py
-----------------------
INCREMENTAL rule-based tagging: when the rules in category_model.json change,
re-tag ONLY the sentences and columns the change can affect, and MERGE the result
into the existing tags table in place — instead of re-running the full engine over
the whole corpus (the full job, voc_topic_model_job.py, takes hours).

WHY THIS IS SAFE (the tag is a pure, deterministic function):
  A category's 0/1 for a sentence depends only on the sentence text (+ a few scope
  attributes) and that category's rule. So if a rule doesn't change, its column
  can't change; and if a rule changes, only sentences that match the OLD or the
  NEW rule can flip.

WHAT IT DOES:
  1. Reads a snapshot of the rules the tags table was last built with
     (<tags_table>__rules_snapshot). If none exists, it SEEDS the snapshot from
     the current rules and exits (run the full job first to populate tags, then
     change rules and re-run this).
  2. Diffs current category_model.json vs the snapshot ->
        changed / added / removed categories (per-lane comparison).
     If the GLOBAL scope filter changed, it aborts: scope moves for every row, so
     a full re-run is required.
  3. AFFECTED COLUMNS = changed + added + removed categories, PLUS their ancestor
     columns (a parent's roll-up flips when a child does). Only these are written.
  4. CANDIDATE ROWS (the incremental win) = rows currently tagged 1 in a changed/
     removed column (may LOSE the tag) UNION rows whose text contains a keyword
     term from the changed/added rule's NEW definition (may GAIN it). This is a
     provable superset of every row that can change; all other rows keep their
     existing tags untouched.
       Fallback: if a changed rule's keywords lane uses fuzzy (term~) or attribute
       (attr:val) seeds that a text pre-filter can't bound, THAT category alone
       degrades to a full in-scope scan (still just its column).
  5. Recomputes the affected columns for candidate rows (same tagger.py engine as
     the full job) and MERGEs them into the tags table. New categories get their
     columns added first; removed categories are zeroed on their currently-1 rows.
  6. Refreshes the frequency table and overwrites the rules snapshot.

The diff / term-extraction helpers are pure Python (no pyspark) so they're
unit-testable and are exercised by test_rule_engine.py's incremental cases.
"""
import json
import os
import re
import sys

CATALOG = "daria_krasavina"
SCHEMA = "gm_voc"
_NS = "%s.%s" % (CATALOG, SCHEMA)

DEFAULTS = {
    "sentence_table": _NS + ".qualtrics_audio_transcripts_sentence_level_sample_data",
    "metadata_table": _NS + ".qualtrics_audio_transcripts_metadata_sample_data",
    "tags_table": _NS + ".voc_classification_rule_tags",
    "freq_table": _NS + ".voc_classification_rule_frequencies",
    # Where to write the incremental result. Empty = update tags_table IN PLACE
    # (default). If set (e.g. <tags_table>_incremental), the job first COPIES the
    # baseline there and applies the change to the copy, leaving the baseline (and
    # its snapshot) untouched — so you can diff baseline vs. post-change.
    "output_table": "",
    # Where the rules-as-of-last-run are recorded. Default: derived from tags_table.
    "snapshot_table": "",
    # Set to "1" to (re)seed the snapshot from current rules and exit (use after a
    # full-job run so the snapshot matches the tags the full job just wrote).
    "seed_only": "0",
}

JOIN_KEY = "natural_id"
TEXT_FIELD = "words"
META_ATTRS = ["call_direction", "cc_lob_mv"]
SENT_ATTRS = ["id_source", "verbatimtype", "language"]
GLOBAL_ID = "__global__"

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


# ---------------------------------------------------------------------------
# Pure-Python planning: diff rules, find affected columns, extract pre-filters.
# ---------------------------------------------------------------------------
def _lanes_equal(a, b):
    keys = ("keywords", "and", "and2", "not")
    na = {k: " ".join((a.get(k) or "").split()) for k in keys}
    nb = {k: " ".join((b.get(k) or "").split()) for k in keys}
    return na == nb


def diff_rules(old_rules, new_rules):
    """Return dict(changed, added, removed, global_changed) of category ids.

    changed  - in both, lanes differ
    added    - only in new
    removed  - only in old
    """
    old = {n["id"]: n["lanes"] for n in old_rules["nodes"]}
    new = {n["id"]: n["lanes"] for n in new_rules["nodes"]}
    changed, added, removed = [], [], []
    for cid, lanes in new.items():
        if cid not in old:
            added.append(cid)
        elif not _lanes_equal(old[cid], lanes):
            changed.append(cid)
    for cid in old:
        if cid not in new:
            removed.append(cid)
    go = (old_rules.get("global_filter") or {}).get("lanes", {})
    gn = (new_rules.get("global_filter") or {}).get("lanes", {})
    return {"changed": changed, "added": added, "removed": removed,
            "global_changed": not _lanes_equal(go, gn)}


def _ancestor_map(rules):
    name_to_id = {n["name"]: n["id"] for n in rules["nodes"]}
    anc = {}
    for n in rules["nodes"]:
        chain = []
        for name in reversed(n.get("path", [])):
            if name in name_to_id:
                chain.append(name_to_id[name])
        anc[n["id"]] = chain
    return anc


def affected_columns(seed_ids, new_rules, old_rules):
    """seed_ids -> those columns plus all their ancestors (roll-up recompute)."""
    anc_new = _ancestor_map(new_rules)
    anc_old = _ancestor_map(old_rules)
    cols = set()
    for cid in seed_ids:
        cols.add(cid)
        for aid in anc_new.get(cid, []) or anc_old.get(cid, []):
            cols.add(aid)
    return cols


# --- keywords-lane term extraction (for the row pre-filter) -----------------
def extract_prefilter(keywords_text):
    """Return (substrings, needs_full_scan).

    substrings   - lowercase literal substrings; a row whose text contains ANY of
                   them is a pre-filter candidate. Built to be a SUPERSET of every
                   sentence the rule's keywords lane can fire on.
    needs_full_scan - True when the keywords lane can fire via fuzzy (term~) or an
                   attribute seed, which a text substring can't bound -> caller
                   must scan all in-scope rows for this category.
    """
    from rule_engine import (And, AttrTerm, Not, Or, PhraseTerm, TrueNode,
                             parse_lane)
    ast = parse_lane(keywords_text or "")
    acc = {"raw": [], "attr": False}

    def walk(node):
        if node is None or isinstance(node, TrueNode):
            return
        if isinstance(node, (Or, And)):
            for c in node.children:
                walk(c)
        elif isinstance(node, Not):
            walk(node.base)          # positive side only; exclusion can't add matches
        elif isinstance(node, PhraseTerm):
            acc["raw"].append(node.raw)
        elif isinstance(node, AttrTerm):
            acc["attr"] = True

    walk(ast)

    subs, full = set(), acc["attr"]
    for raw in acc["raw"]:
        if raw.startswith('"'):                       # phrase / proximity phrase
            inner = re.sub(r'"~\d+$', "", raw).strip('"')
            words = re.findall(r"[a-z0-9]+", inner.lower())
            if words:
                subs.add(max(words, key=len))         # longest word: must be present
            continue
        if raw.endswith("~"):                          # single-term fuzzy
            full = True
            continue
        runs = re.findall(r"[a-z0-9]+", raw.lower())   # strip * ? and punctuation
        if not runs:
            continue
        run_ = max(runs, key=len)
        if len(run_) >= 2:
            subs.add(run_)
        else:
            full = True                                # too short to pre-filter safely
    return sorted(subs), full


def prefilter_regex(substrings):
    """rlike alternation matching any of the substrings (regex-escaped)."""
    if not substrings:
        return None
    return "(%s)" % "|".join(re.escape(s) for s in substrings)


def plan_incremental(old_rules, new_rules):
    """Full plan dict. See module docstring for field meanings."""
    d = diff_rules(old_rules, new_rules)
    seeds = d["changed"] + d["added"] + d["removed"]
    cols = affected_columns(seeds, new_rules, old_rules)

    new_lanes = {n["id"]: n["lanes"] for n in new_rules["nodes"]}
    prefilter_subs, full_scan = set(), False
    for cid in d["changed"] + d["added"]:
        subs, full = extract_prefilter((new_lanes.get(cid) or {}).get("keywords", ""))
        prefilter_subs.update(subs)
        full_scan = full_scan or full

    return {
        "changed": d["changed"], "added": d["added"], "removed": d["removed"],
        "global_changed": d["global_changed"],
        "affected_columns": sorted(cols),
        "prefilter_substrings": sorted(prefilter_subs),
        "needs_full_scan": full_scan,
        "nothing_to_do": not seeds and not d["global_changed"],
    }


# ---------------------------------------------------------------------------
# Params
# ---------------------------------------------------------------------------
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
    if not params.get("snapshot_table"):
        params["snapshot_table"] = params["tags_table"] + "__rules_snapshot"
    return params


# ---------------------------------------------------------------------------
# Snapshot IO (rules-as-of-last-run, stored as a small Delta table)
# ---------------------------------------------------------------------------
def write_snapshot(spark, table, rules):
    rows = [(GLOBAL_ID, json.dumps((rules.get("global_filter") or {}).get("lanes", {})),
             "[]", "", False)]
    for n in rules["nodes"]:
        rows.append((n["id"], json.dumps(n["lanes"]), json.dumps(n.get("path", [])),
                     n["name"], bool(n.get("comparison_target", False))))
    df = spark.createDataFrame(rows, ["category_id", "lanes_json", "path_json",
                                      "name", "is_target"])
    (df.write.mode("overwrite").format("delta")
        .option("overwriteSchema", "true").saveAsTable(table))


def read_snapshot(spark, table):
    """Rebuild a rules-like dict {global_filter, nodes[]} from the snapshot table."""
    rows = spark.table(table).collect()
    gf, nodes = None, []
    for r in rows:
        lanes = json.loads(r["lanes_json"])
        if r["category_id"] == GLOBAL_ID:
            gf = {"name": "DBX POC", "lanes": lanes}
        else:
            nodes.append({"id": r["category_id"], "name": r["name"],
                          "path": json.loads(r["path_json"]), "lanes": lanes,
                          "comparison_target": bool(r["is_target"])})
    return {"global_filter": gf, "nodes": nodes}


# ---------------------------------------------------------------------------
# Recompute UDF (affected categories only) — same engine as the full job.
# ---------------------------------------------------------------------------
def _make_tag_udf():
    import pandas as pd
    from pyspark.sql.functions import pandas_udf
    from pyspark.sql.types import StringType

    _state = {}

    def _tagger():
        if "t" not in _state:
            from tagger import build_tagger, load_rules
            _state["t"] = build_tagger(load_rules())
        return _state["t"]

    attr_cols = SENT_ATTRS + META_ATTRS

    @pandas_udf(StringType())
    def tag_udf(payload: "pd.DataFrame") -> "pd.Series":
        tg = _tagger()
        out = []
        for _, row in payload.iterrows():
            text = row["words"] or ""
            attrs = {c: row[c] for c in attr_cols}
            topics = tg.tag(text, attrs)          # {} if out of scope
            out.append(json.dumps({tid: v["terms"] for tid, v in topics.items()}))
        return pd.Series(out)

    return tag_udf, attr_cols


def run():
    import time

    from pyspark.sql import SparkSession
    from pyspark.sql import functions as F
    from pyspark.sql.types import ArrayType, MapType, StringType

    from tagger import build_tagger, load_rules

    spark = SparkSession.builder.appName("gm_voc_incremental_rules").getOrCreate()
    params = get_params()
    print("Params: %s" % params)
    tags_table = params["tags_table"]
    snap_table = params["snapshot_table"]
    new_rules = load_rules()

    # Seed-and-exit if there is no snapshot yet (or explicitly requested).
    if params.get("seed_only") == "1" or not spark.catalog.tableExists(snap_table):
        write_snapshot(spark, snap_table, new_rules)
        print("Seeded rules snapshot at %s. Nothing to apply.\n"
              "  -> Change category_model.json, then re-run to apply incrementally."
              % snap_table)
        return

    old_rules = read_snapshot(spark, snap_table)
    plan = plan_incremental(old_rules, new_rules)
    print("Plan: %s" % json.dumps(plan, indent=2))

    if plan["global_changed"]:
        raise SystemExit(
            "Global scope filter changed -> scope moves for EVERY row. Re-run the "
            "full job (voc_topic_model_job.py); incremental cannot bound this.")
    if plan["nothing_to_do"]:
        print("No rule changes detected. Nothing to do.")
        return

    changed_removed = plan["changed"] + plan["removed"]
    affected = plan["affected_columns"]

    # Target: update the baseline IN PLACE, or COPY it to a separate _incremental
    # table and apply the change there (baseline + snapshot left untouched).
    target = (params.get("output_table") or "").strip() or tags_table
    if target != tags_table:
        spark.sql("CREATE OR REPLACE TABLE %s AS SELECT * FROM %s" % (target, tags_table))
        print("Copied baseline %s -> %s; applying incremental to the copy."
              % (tags_table, target))

    tags = spark.table(target)
    existing_cols = set(tags.columns)

    # ---- CANDIDATE ROWS -----------------------------------------------------
    conds = []
    # (a) rows currently tagged 1 in a changed/removed column -> may LOSE the tag.
    for cid in changed_removed:
        if cid in existing_cols:
            conds.append(F.col(cid) == 1)
    # (b) rows whose text contains a NEW keyword term -> may GAIN the tag.
    rx = prefilter_regex(plan["prefilter_substrings"])
    if rx:
        conds.append(F.lower(F.col(TEXT_FIELD)).rlike(rx))
    # Fallback: unbounded seeds -> must consider all in-scope rows.
    if plan["needs_full_scan"] or not conds:
        print("Full in-scope scan for affected columns (unbounded keyword seeds "
              "or no pre-filter available).")
        candidates = tags
    else:
        cond = conds[0]
        for c in conds[1:]:
            cond = cond | c
        candidates = tags.filter(cond)

    n_total = tags.count()
    n_cand = candidates.count()
    print("Candidate rows: %d of %d (%.2f%%) | affected columns: %s"
          % (n_cand, n_total, 100.0 * n_cand / n_total if n_total else 0.0, affected))

    # ---- RECOMPUTE affected columns on candidates ---------------------------
    meta = spark.table(params["metadata_table"])
    meta_cols = [JOIN_KEY] + [c for c in META_ATTRS if c in meta.columns]
    meta = meta.select(*meta_cols).dropDuplicates([JOIN_KEY])
    cand = candidates.select(JOIN_KEY, TEXT_FIELD, *[c for c in SENT_ATTRS if c in existing_cols])
    cand = cand.join(meta, on=JOIN_KEY, how="left")

    tag_udf, attr_cols = _make_tag_udf()
    payload = [F.col(TEXT_FIELD).alias("words")]
    for c in attr_cols:
        payload.append((F.col(c) if c in cand.columns else F.lit(None).cast("string")).alias(c))
    cand = cand.withColumn("_tj", tag_udf(F.struct(*payload)))
    cand = cand.withColumn("_tp", F.from_json("_tj", MapType(StringType(), ArrayType(StringType()))))

    recomputed_cols = [cid for cid in affected if cid not in plan["removed"]]
    sel = [F.col(JOIN_KEY)]
    for cid in recomputed_cols:
        sel.append(F.when(F.col("_tp").getItem(cid).isNotNull(), F.lit(1))
                   .otherwise(F.lit(0)).alias(cid))
        sel.append(F.concat_ws("; ", F.coalesce(F.col("_tp").getItem(cid),
                   F.array().cast("array<string>"))).alias(cid + "__terms"))
    recomputed = cand.select(*sel)

    # Materialize the recompute once (serverless-safe: no persist), then MERGE.
    tmp = target + "__inc_tmp"
    (recomputed.write.mode("overwrite").format("delta")
        .option("overwriteSchema", "true").saveAsTable(tmp))

    # ---- Schema: add columns for newly added categories ---------------------
    for cid in plan["added"]:
        for col, typ in ((cid, "INT"), (cid + "__terms", "STRING")):
            if col not in existing_cols:
                spark.sql("ALTER TABLE %s ADD COLUMNS (`%s` %s)" % (target, col, typ))

    # ---- MERGE recomputed values into the target ----------------------------
    set_parts = []
    for cid in recomputed_cols:
        set_parts.append("t.`%s` = s.`%s`" % (cid, cid))
        set_parts.append("t.`%s` = s.`%s`" % (cid + "__terms", cid + "__terms"))
    # Removed categories: zero out their currently-1 rows (which are candidates).
    for cid in plan["removed"]:
        if cid in existing_cols:
            set_parts.append("t.`%s` = 0" % cid)
            if cid + "__terms" in existing_cols:
                set_parts.append("t.`%s` = ''" % (cid + "__terms"))
    merge_sql = ("MERGE INTO %s t USING %s s ON t.`%s` = s.`%s` "
                 "WHEN MATCHED THEN UPDATE SET %s"
                 % (target, tmp, JOIN_KEY, JOIN_KEY, ", ".join(set_parts)))
    t0 = time.time()
    spark.sql(merge_sql)
    print("MERGE done in %.1fs (updated affected columns on candidate rows)."
          % (time.time() - t0))
    spark.sql("DROP TABLE IF EXISTS %s" % tmp)

    # ---- Refresh frequency table + snapshot ---------------------------------
    # In place: refresh the freq table and advance the snapshot. Separate output:
    # leave the baseline's freq + snapshot untouched (repeatable diffs); the
    # _incremental table is a comparison artifact.
    if target == tags_table:
        _refresh_frequencies(spark, params, new_rules)
        write_snapshot(spark, snap_table, new_rules)
        print("Updated %s in place; refreshed %s and snapshot %s."
              % (tags_table, params["freq_table"], snap_table))
    else:
        print("Wrote incremental result to %s; baseline %s, its freq %s and snapshot "
              "%s unchanged (diff the two to see what the change did)."
              % (target, tags_table, params["freq_table"], snap_table))


def _refresh_frequencies(spark, params, rules):
    from pyspark.sql import functions as F

    from tagger import build_tagger
    tagger = build_tagger(rules)
    ids = tagger.topic_ids()
    meta = tagger.topic_meta()
    tags = spark.table(params["tags_table"])
    present = [cid for cid in ids if cid in tags.columns]
    agg = []
    for cid in present:
        agg.append(F.sum(F.col(cid)).alias(cid + "_s"))
        agg.append(F.countDistinct(F.when(F.col(cid) == 1, F.col("id_document"))).alias(cid + "_d"))
    row = tags.agg(*agg).collect()[0].asDict() if agg else {}
    freq_rows = [(cid, meta[cid]["name"], " > ".join(meta[cid]["path"]),
                  bool(meta[cid]["is_target"]), int(row.get(cid + "_s") or 0),
                  int(row.get(cid + "_d") or 0)) for cid in present]
    freq_df = spark.createDataFrame(freq_rows, ["topic_id", "topic", "path",
                                    "is_comparison_target", "sentences_tagged",
                                    "documents_tagged"])
    (freq_df.write.mode("overwrite").format("delta")
        .option("overwriteSchema", "true").saveAsTable(params["freq_table"]))


if __name__ == "__main__":
    run()
