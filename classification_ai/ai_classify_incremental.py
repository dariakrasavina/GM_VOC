"""
ai_classify_incremental.py
--------------------------
INCREMENTAL re-tag for the ai_classify track — the semantic analogue of
classification_rule_engine/incremental_rule_job.py.

For ai_classify, a category's "rule" is its LABEL DESCRIPTION in
category_model.json (the text ai_classify reads). A "rule change" = editing that
description. This job re-scores ONLY the changed label(s) over a bounded set of
candidate sentences and MERGEs the result into the existing ai_classify tags
table in place — instead of re-running the full ai_classify job over the whole
corpus for every label (which takes hours + AI-function DBUs each time).

WHY THIS IS DIFFERENT FROM THE RULE-ENGINE INCREMENTAL JOB
  The rule engine's rule literally contains the keyword (e.g. 'snowfall'), so the
  rows that can newly GAIN a tag are found with a cheap substring pre-filter. An
  ai_classify description is SEMANTIC — there is no literal term to filter on, so
  a broadening change ("also count blizzards") can make ANY sentence qualify.
  That forces one of three candidate strategies (the `mode` param):

    full   (default, always correct) - re-score the changed label(s) over ALL
             distinct in-scope sentences. Correct for any change (broaden or
             narrow), but pays inference on every distinct sentence for the
             changed label(s). Still far cheaper than the full job: only the
             changed labels are scored (not all categories), deduped by sentence.
    narrow (cheap; restrictive changes only) - re-score only sentences CURRENTLY
             tagged in a changed/removed label. Correct only when the change can
             just REMOVE tags (tightened description). Misses gains, so do NOT use
             for a broadening change or when labels were added.
    terms  (cheap; broadening w/ a keyword proxy) - re-score sentences currently
             tagged in a changed label UNION sentences whose text matches
             `candidate_terms`. Mirrors the rule-engine pre-filter, but YOU supply
             the proxy terms, so it can miss paraphrases that don't contain them.

WHAT IT DOES
  1. Reads a snapshot of the label descriptions the tags table was last built with
     (<ai_tags_table>__desc_snapshot, a one-row {label: description} JSON blob). If
     none exists, it SEEDS the snapshot from the current descriptions and exits
     (run the full ai_classify job first, then change a description and re-run).
  2. Diffs current category_model.json target descriptions vs the snapshot ->
     changed / added / removed labels. `changed_labels` can override the diff
     (explicit target category NAMES) for a forced demo re-score.
  3. Builds the CANDIDATE distinct sentences per `mode` (above).
  4. Re-scores ONLY the changed+added labels with ai_classify() on the warehouse
     (single small labels object, confidence-gated exactly like the full job),
     rebuilds each candidate sentence's label set = (its old labels minus the
     changed set) + (changed labels that now apply), and recomputes EVERY category
     0/1 column (leaves + roll-up parents) from that set via the same arrays_overlap
     roll-up as ai_classify.py.
  5. MERGEs the updated ai_labels + recomputed columns into the tags table, keyed
     by sentence text (identical text shares one classification), and overwrites
     the snapshot.

CAVEAT: ai_result_json (the full multi-label audit blob) is NOT refreshed here —
it still reflects the last FULL run, because we only re-ran the changed label(s).
The load-bearing fields (ai_labels + the 0/1 columns) ARE updated. Re-run the full
ai_classify job when you need a fresh full-response audit blob.

RUN: as a Databricks notebook/job task (run_ai_classify_incremental_notebook.py)
or standalone (python ai_classify_incremental.py --key=value). Requires a SQL
warehouse id and a Model-Serving-supported region — same execution path as
ai_classify.py.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# Reuse the full job's roll-up, label formatting, result schema, instructions and
# text field so the incremental output is byte-compatible with a full run.
import ai_classify as base
from ai_common import load_categories

TEXT_FIELD = base.TEXT_FIELD

_TRIGGER_COLS = {}


def _trigger_cols(rules_path=None):
    """Cached base._trigger_cols() — it reads the category model and runs an
    O(n^2) descendant scan, so compute the roll-up column list once per model
    file per process (keyed by rules_path so v1 and v2 don't collide)."""
    if rules_path not in _TRIGGER_COLS:
        _TRIGGER_COLS[rules_path] = base._trigger_cols(rules_path)
    return _TRIGGER_COLS[rules_path]

CATALOG = "daria_krasavina"
SCHEMA = "gm_voc"
_NS = "%s.%s" % (CATALOG, SCHEMA)

DEFAULTS = {
    # The existing ai_classify output table (the FULL-run baseline). It already
    # holds every in-scope row (tagged or not) with ai_labels + one 0/1 column per
    # category, so candidates are drawn from it (no source scan needed).
    "ai_tags_table": _NS + ".voc_classification_ai_classify_tags",
    # Where to write the incremental result. Empty = update ai_tags_table IN PLACE
    # (default). If set (e.g. <ai_tags_table>_incremental), the job first COPIES the
    # baseline there and applies the change to the copy, leaving the baseline (and
    # its snapshot) untouched — so you can diff baseline vs. post-change.
    "output_table": "",
    # Descriptions-as-of-last-run. Default: derived from ai_tags_table.
    "snapshot_table": "",
    # Set "1" to (re)seed the snapshot from current descriptions and exit (use
    # after a full ai_classify run so the snapshot matches what it wrote).
    "seed_only": "0",
    # Optional explicit target category NAMES (comma-separated) to re-score,
    # overriding the snapshot diff. Handy for a demo ("re-score Points - Redeem").
    "changed_labels": "",
    # Candidate strategy: full | narrow | terms (see module docstring).
    "mode": "full",
    # For mode=terms: comma-separated lowercase substrings; a sentence whose text
    # contains ANY of them is a GAIN candidate (proxy for the broadened concept).
    "candidate_terms": "",
    # Confidence gate — MUST match the full run's setting so tags stay consistent.
    "enable_confidence": "true",
    "conf_threshold": "0.0",
    # Category model to diff + re-score against. Empty = the default
    # shared/category_model.json (v1). Set to another basename synced with the
    # bundle (e.g. category_model_v2.json) to re-tag against a different label set.
    # MUST match the model the baseline was built with, or the diff is meaningless.
    "category_model": "",
    # REQUIRED for run(): the SQL warehouse that executes the batch SQL.
    "warehouse_id": "",
}


# ---------------------------------------------------------------------------
# Pure-Python planning (unit-testable; no pyspark / no SDK).
# ---------------------------------------------------------------------------
def diff_descriptions(old_desc, new_desc):
    """old/new are {label_name: description}. Return dict(changed, added, removed)."""
    changed, added = [], []
    for name, desc in new_desc.items():
        if name not in old_desc:
            added.append(name)
        elif " ".join((old_desc[name] or "").split()) != " ".join((desc or "").split()):
            changed.append(name)
    removed = [name for name in old_desc if name not in new_desc]
    return {"changed": changed, "added": added, "removed": removed}


def terms_regex(candidate_terms):
    """rlike alternation from a comma-separated term list (regex-escaped), or None."""
    terms = [t.strip().lower() for t in (candidate_terms or "").split(",") if t.strip()]
    if not terms:
        return None
    return "(%s)" % "|".join(re.escape(t) for t in terms)


def candidate_condition(mode, loss_col_ids, rx):
    """SQL WHERE predicate (on the tags table) selecting candidate rows.

    loss_col_ids - category-id columns whose current 1-rows may LOSE a tag.
    rx           - terms regex for GAIN candidates (mode=terms), or None.
    Returns a SQL string, or "" meaning "all rows" (mode=full).
    """
    if mode == "full":
        return ""
    loss = " OR ".join("`%s` = 1" % cid for cid in loss_col_ids)
    if mode == "narrow":
        return loss or "false"      # no loss columns -> nothing can change
    if mode == "terms":
        gain = ("lower(%s) rlike '%s'" % (TEXT_FIELD, rx)) if rx else None
        parts = [p for p in (loss, gain) if p]
        return " OR ".join("(%s)" % p for p in parts) if parts else "false"
    raise ValueError("mode must be one of: full, narrow, terms (got %r)" % mode)


def plan(old_desc, new_desc, explicit_changed):
    """Resolve the label plan. explicit_changed (list of names) overrides the diff."""
    if explicit_changed:
        d = {"changed": [n for n in explicit_changed if n in new_desc],
             "added": [], "removed": []}
    else:
        d = diff_descriptions(old_desc, new_desc)
    d["rescore"] = d["changed"] + d["added"]          # labels to send to ai_classify
    d["touched"] = d["changed"] + d["added"] + d["removed"]
    d["nothing_to_do"] = not d["touched"]
    return d


# ---------------------------------------------------------------------------
# SQL builder
# ---------------------------------------------------------------------------
def build_statements(params, pl, target_labels, name_to_id):
    """Return the ordered SQL steps + bound-param payloads for a plan `pl`."""
    rules_path = params.get("category_model") or None
    tags = params["ai_tags_table"]
    cand = tags + "__inc_cand"
    merged = tags + "__inc_merge"

    # Columns that may LOSE a tag = current leaf columns of changed/removed labels.
    loss_ids = [name_to_id[n] for n in (pl["changed"] + pl["removed"]) if n in name_to_id]
    rx = terms_regex(params.get("candidate_terms"))
    where = candidate_condition(params.get("mode", "full"), loss_ids, rx)
    where_sql = ("WHERE %s" % where) if where else ""

    # (1) candidate DISTINCT sentences + their existing label set (identical per text).
    s_cand = ("CREATE OR REPLACE TABLE %s AS "
              "SELECT %s, max(ai_labels) AS old_labels FROM %s %s GROUP BY %s"
              % (cand, TEXT_FIELD, tags, where_sql, TEXT_FIELD))

    # confidence gate for the re-scored labels (same logic as ai_classify.py).
    conf = "true" if str(params.get("enable_confidence")).lower() == "true" else "false"
    try:
        thr = float(params.get("conf_threshold") or 0)
    except ValueError:
        thr = 0.0
    resp = "r.response"
    if conf == "true" and thr > 0:
        resp = ("filter(%s, x -> x.confidence_score IS NOT NULL "
                "AND x.confidence_score >= %s)" % (resp, thr))
    applied_expr = "transform(%s, x -> x.value)" % resp

    touched_arr = base._sql_array(pl["touched"])       # drop old entries for these
    # Recompute every category column (leaf + parents) from the rebuilt label set.
    col_exprs = []
    for cid, names in _trigger_cols(rules_path):
        if names:
            col_exprs.append("CASE WHEN arrays_overlap(lbls, %s) THEN 1 ELSE 0 END AS `%s`"
                             % (base._sql_array(names), cid))
        else:
            col_exprs.append("CAST(0 AS INT) AS `%s`" % cid)

    # (2) re-score changed labels, rebuild the label set, recompute all columns.
    s_merge = (
        "CREATE OR REPLACE TABLE %s AS "
        "WITH scored AS ("
        "  SELECT %s, old_labels, from_json(to_json(ai_classify(%s, :labels, "
        "  map('version','2.1','multilabel','true','enableConfidenceScores','%s',"
        "  'instructions', :instructions))), '%s') AS r FROM %s), "
        "rebuilt AS ("
        "  SELECT %s, "
        "  array_distinct(array_union("
        "    array_except(from_json(old_labels,'array<string>'), %s), %s)) AS lbls "
        "  FROM scored) "
        "SELECT %s, to_json(lbls) AS ai_labels, %s FROM rebuilt"
        % (merged, TEXT_FIELD, TEXT_FIELD, conf, base._RESULT_SCHEMA, cand,
           TEXT_FIELD, touched_arr, applied_expr,
           TEXT_FIELD, ", ".join(col_exprs)))

    # (3) MERGE updated ai_labels + recomputed columns back, keyed by sentence text.
    set_parts = ["t.ai_labels = s.ai_labels"]
    current_ids = [cid for cid, _ in _trigger_cols(rules_path)]
    for cid in current_ids:
        set_parts.append("t.`%s` = s.`%s`" % (cid, cid))
    # Removed categories are no longer recomputed above -> zero their column here.
    for n in pl["removed"]:
        cid = name_to_id.get(n)
        if cid and cid not in current_ids:
            set_parts.append("t.`%s` = 0" % cid)
    s_merge_into = ("MERGE INTO %s t USING %s s ON t.%s = s.%s "
                    "WHEN MATCHED THEN UPDATE SET %s"
                    % (tags, merged, TEXT_FIELD, TEXT_FIELD, ", ".join(set_parts)))

    drops = ["DROP TABLE IF EXISTS %s" % merged, "DROP TABLE IF EXISTS %s" % cand]

    # labels object sent to ai_classify = ONLY the changed/added labels (+ defs).
    rescore_labels = {n: (target_labels.get(n) or n)[:1000] for n in pl["rescore"]}
    return {"cand": s_cand, "merge": s_merge, "merge_into": s_merge_into, "drops": drops,
            "labels": json.dumps(rescore_labels), "instructions": base.INSTRUCTIONS}


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
        params["snapshot_table"] = params["ai_tags_table"] + "__desc_snapshot"
    return params


def run():
    import time

    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.sql import (
        ExecuteStatementRequestOnWaitTimeout, StatementParameterListItem)

    params = get_params()
    print("Params: %s" % params)
    wid = (params.get("warehouse_id") or "").strip()
    if not wid:
        raise ValueError("warehouse_id is required (the SQL warehouse that runs the "
                         "batch ai_classify). Pass it as a job parameter / widget.")
    snap = params["snapshot_table"]
    w = WorkspaceClient()

    def execute(sql, parameters=None, label="", fetch=False):
        t0 = time.time()
        r = w.statement_execution.execute_statement(
            warehouse_id=wid, statement=sql, parameters=parameters,
            wait_timeout="50s",
            on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CONTINUE)
        while r.status.state.value in ("PENDING", "RUNNING"):
            time.sleep(5)
            r = w.statement_execution.get_statement(r.statement_id)
        if r.status.state.value != "SUCCEEDED":
            raise RuntimeError("statement failed (%s): %s" % (label, r.status.error))
        if label:
            print("  %-10s done in %.1fs" % (label, time.time() - t0))
        if fetch:
            data = r.result.data_array if (r.result and r.result.data_array) else []
            return data
        return r

    # Current target descriptions = the ai_classify "rules".
    rules_path = params.get("category_model") or None
    target_labels, name_to_id, _, _, _ = load_categories(rules_path)
    new_desc = {n: d for n, d in target_labels.items()}

    def table_exists(t):
        try:
            execute("SELECT 1 FROM %s LIMIT 1" % t)
            return True
        except RuntimeError:
            return False

    def write_snapshot():
        execute("CREATE OR REPLACE TABLE %s AS SELECT :j AS labels_json" % snap,
                parameters=[StatementParameterListItem(name="j", value=json.dumps(new_desc))],
                label="snapshot")

    # Seed-and-exit if there is no snapshot yet (or explicitly requested).
    if params.get("seed_only") == "1" or not table_exists(snap):
        write_snapshot()
        print("Seeded description snapshot at %s. Nothing to apply.\n"
              "  -> Edit a target category's description in category_model.json, "
              "then re-run to apply incrementally." % snap)
        return

    old_rows = execute("SELECT labels_json FROM %s" % snap, fetch=True)
    old_desc = json.loads(old_rows[0][0]) if old_rows else {}

    explicit = [n.strip() for n in (params.get("changed_labels") or "").split(",") if n.strip()]
    pl = plan(old_desc, new_desc, explicit)
    print("Plan: %s" % json.dumps({k: pl[k] for k in
          ("changed", "added", "removed", "rescore")}, indent=2))
    if pl["nothing_to_do"]:
        print("No description changes detected. Nothing to do.")
        return
    if params.get("mode") == "narrow" and pl["added"]:
        print("WARNING: mode=narrow cannot find GAIN candidates for ADDED labels "
              "(%s). Use mode=full or mode=terms to catch newly-qualifying "
              "sentences." % ", ".join(pl["added"]))

    # Target: in place (baseline) or a separate _incremental copy.
    tags = params["ai_tags_table"]
    out = (params.get("output_table") or "").strip() or tags
    if out != tags:
        execute("CREATE OR REPLACE TABLE %s AS SELECT * FROM %s" % (out, tags), label="copy")
        print("  copied baseline %s -> %s (baseline untouched)" % (tags, out))
    params_out = dict(params)
    params_out["ai_tags_table"] = out          # all writes/temps target `out`

    st = build_statements(params_out, pl, target_labels, name_to_id)
    print("Re-scoring labels: %s | mode=%s | target=%s" % (pl["rescore"], params.get("mode"), out))

    execute(st["cand"], label="candidates")
    n_cand = execute("SELECT count(*) FROM %s" % (out + "__inc_cand"), fetch=True)
    print("  distinct candidate sentences: %s" % (n_cand[0][0] if n_cand else "?"))
    if pl["rescore"]:
        execute(st["merge"],
                parameters=[StatementParameterListItem(name="labels", value=st["labels"]),
                            StatementParameterListItem(name="instructions", value=st["instructions"])],
                label="classify")
    else:
        # Only removals: rebuild label set with no re-score (empty ai_classify input
        # would error), so build the merge table directly from candidates.
        execute(_removal_only_merge_sql(params_out, pl, name_to_id), label="remove")
    execute(st["merge_into"], label="merge")
    for d in st["drops"]:
        execute(d, label="cleanup")
    if out == tags:
        write_snapshot()
        print("Updated %s in place (incremental) and refreshed snapshot %s." % (tags, snap))
    else:
        print("Wrote incremental result to %s; baseline %s and snapshot %s unchanged "
              "(diff the two to see what the change did)." % (out, tags, snap))


def _removal_only_merge_sql(params, pl, name_to_id):
    """Merge-table build when the only change is REMOVED labels (no re-score)."""
    rules_path = params.get("category_model") or None
    tags = params["ai_tags_table"]
    cand = tags + "__inc_cand"
    merged = tags + "__inc_merge"
    touched_arr = base._sql_array(pl["removed"])
    col_exprs = []
    for cid, names in _trigger_cols(rules_path):
        if names:
            col_exprs.append("CASE WHEN arrays_overlap(lbls, %s) THEN 1 ELSE 0 END AS `%s`"
                             % (base._sql_array(names), cid))
        else:
            col_exprs.append("CAST(0 AS INT) AS `%s`" % cid)
    return ("CREATE OR REPLACE TABLE %s AS WITH rebuilt AS ("
            "SELECT %s, array_except(from_json(old_labels,'array<string>'), %s) AS lbls "
            "FROM %s) SELECT %s, to_json(lbls) AS ai_labels, %s FROM rebuilt"
            % (merged, TEXT_FIELD, touched_arr, cand, TEXT_FIELD, ", ".join(col_exprs)))


if __name__ == "__main__":
    run()
