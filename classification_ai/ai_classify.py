"""
ai_classify.py
----------------------
Third AI classification variant for the bake-off: uses the Databricks BUILT-IN
ai_classify() SQL function, **Version 2.1** (the recommended, purpose-built path
for label classification).

  ai_classify(text, labels, MAP('version','2.1', ...))

v2.1 removes the limitations the earlier version forced us to work around:
  * LABEL DESCRIPTIONS  - labels are a JSON object {label: definition}, so the
    model sees each category's business definition (no more cryptic <=50-char
    labels). Label key 1-100 chars; description 0-1000 chars.
  * MULTI-LABEL         - 'multilabel'='true' returns ALL applicable categories
    (matching the rule engine and Qualtrics), so we no longer need a synthetic
    "None of the above" opt-out: a sentence that matches nothing returns an empty
    response.
  * CONFIDENCE SCORES   - 'enableConfidenceScores'='true' returns a 0-1 score per
    label. We GATE on it (conf_threshold) to fix over-tagging: multilabel returns
    every weakly-applicable label, so without a cut the roll-up fires on 0.3-
    confidence noise exactly like a 0.98 hit. The full (ungated) response is still
    stored in ai_result_json, so the threshold can be re-tuned offline / audited.
  * RATIONALES          - 'enableRationales'='true' returns a short justification
    (OFF by default: extra output tokens; enable only for QA).
  * GLOBAL INSTRUCTIONS - a task description that steers the managed model the way
    the ai_query prompt steers ai_query.

Return shape (v2.1) is a struct:
  { response: [ { value, confidence_score, rationale } ], error_message }
We extract the matched label NAMES (response[*].value), then roll them up to
parent categories with arrays_overlap — the SAME roll-up the ai_query_sql path
uses — so the output has one 0/1 column per category (leaves + parents) and lines
up with the rule engine and the compare job.

TRADE-OFFS vs the ai_query approach:
  + Purpose-built for classification; no prompt engineering beyond instructions.
  + Multi-label + descriptions + confidence, natively.
  - FIXED Databricks-managed model (you CANNOT choose Claude/GPT as with ai_query),
    so the model's quality ceiling is capped — evaluate via the compare job.

Runs as DBSQL batch on a SQL warehouse (same execution path as ai_query_sql).
"""
import json
import os
import sys

CATALOG = "daria_krasavina"
SCHEMA = "gm_voc"
_NS = "%s.%s" % (CATALOG, SCHEMA)

DEFAULTS = {
    "sentence_table": _NS + ".qualtrics_audio_transcripts_sentence_level_sample_data",
    "ai_tags_table": _NS + ".voc_classification_ai_classify_tags",
    "classify_date": "2026-06-11",
    # Optional hour-of-day window within that day (both empty = whole day;
    # hour_end EXCLUSIVE). e.g. hour_start=9, hour_end=12 -> 09:00:00-11:59:59.
    "hour_start": "",
    "hour_end": "",
    # Drop sentences shorter than this many words before classifying (0 = off).
    "min_words": "0",
    # Distinct sentences to classify (0 = all distinct). When capped (>0),
    # sample_mode decides which ones.
    "sample_limit": "0",
    # How to pick when sample_limit>0: "random" (default; representative sample —
    # avoids over-focusing on high-frequency filler) or "frequency" (most-common
    # first = max row coverage for a capped PRODUCTION run). Moot when
    # sample_limit=0 (every distinct sentence is classified either way).
    "sample_mode": "random",
    # v2.1 feature toggles. Confidence is cheap and load-bearing here (the
    # over-tagging gate depends on it) so keep it ON; rationales add output tokens
    # (best practice: keep output small) so default them OFF.
    "enable_confidence": "true",
    "enable_rationales": "false",
    # OVER-TAGGING GATE (primary fix). Keep a returned label only if its
    # confidence_score >= conf_threshold. 0.0 = keep all (old behavior). Requires
    # enable_confidence=true. CALIBRATE this against the rule engine with
    # calibrate_confidence_threshold.sql rather than guessing — then set it here.
    "conf_threshold": "0.0",
    # Optional blunt secondary control: cap each sentence to its top-N labels by
    # confidence (0 = no cap). Useful if a few sentences still over-tag after the
    # threshold; leave off once the threshold is calibrated.
    "max_labels": "0",
    # Category model to read label names + descriptions from. Empty = the default
    # shared/category_model.json (v1). Set to another basename synced with the
    # bundle (e.g. category_model_v2.json) to classify against a different label
    # set — same knob as the rule engine's category_model param.
    "category_model": "",
    # REQUIRED for run(): the SQL warehouse that executes the batch SQL.
    "warehouse_id": "",
}

# Global, CATEGORY-AGNOSTIC task instructions for the managed model (max 20,000
# chars). Per-category INCLUDE/EXCLUDE guidance lives in each target node's
# `ai_description` in the category_model JSON (falling back to the customer's
# `description` if absent) — load_categories reads it, the labels object carries it
# to the model, and the incremental job can DIFF it. So adding or tightening a
# category is an ai_description edit in the model file — no code change, and the
# customer's `description` is left untouched. Keep THIS string to guidance that
# applies to EVERY label (customer-only, ignore IVR/marketing, most sentences match
# nothing). Mirrors the preamble of ai_common.build_prompt (the ai_query path).
INSTRUCTIONS = (
    "You are labeling a customer's sentence from a call-center (contact center) "
    "transcript. Classify ONLY the customer's own words; ignore any automated system "
    "prompts, IVR menu options, hold messages, and marketing/opt-in text that may "
    "appear. Assign a category ONLY when the sentence CLEARLY and EXPLICITLY "
    "expresses it. MOST sentences match nothing - return no labels for greetings, "
    "small talk, back-channel, acknowledgments, hesitation/thinking-aloud, and "
    "generic or short clarifying questions that carry no category meaning (e.g. "
    "'What?', 'Huh?', 'Pardon me?', 'Get what?', 'I don't know.', 'I do not know "
    "what to do.', 'I'm not sure.', 'Okay.', 'Um.'). Those are NOT categories. "
    "Each category's definition states exactly when to assign it and what to "
    "exclude - follow those definitions precisely."
)

# v2.1 return struct (missing optional fields become null via from_json).
_RESULT_SCHEMA = ("struct<response:array<struct<value:string,"
                  "confidence_score:double,rationale:string>>,error_message:string>")

TEXT_FIELD = "words"
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from ai_common import load_categories


def build_labels_json(rules_path=None):
    """JSON object {category_name: definition} for the target (leaf) categories.

    v2.1 labels-with-descriptions format. Key = category name (1-100 chars),
    value = its business definition (0-1000 chars). Returned label `value`s are
    these keys, which we map back to categories for roll-up. `rules_path` (the
    category_model param) selects which model file the labels come from.
    """
    target_labels, _, _, _, _ = load_categories(rules_path)
    # load_categories already trims descriptions; keep well under the 1000-char cap.
    return json.dumps({name: (desc or name)[:1000] for name, desc in target_labels.items()})


def _trigger_cols(rules_path=None):
    """[(category_id, [label names that turn it on]) ...] for the roll-up.

    A category's column is 1 when the returned labels overlap: the category's own
    name (if it is a target) plus the names of any target descendants (roll-up).
    Identical logic to ai_query_sql — the two share the same output shape.
    """
    target_labels, name_to_id, all_ids, ancestors, meta = load_categories(rules_path)
    target_names = set(target_labels.keys())
    cols = []
    for cid in all_ids:
        names = set()
        if meta[cid]["name"] in target_names:
            names.add(meta[cid]["name"])
        for d in all_ids:                       # d a descendant of cid?
            if cid in ancestors.get(d, []) and meta[d]["name"] in target_names:
                names.add(meta[d]["name"])
        cols.append((cid, sorted(names)))
    return cols


def _sql_array(names):
    return "array(%s)" % ", ".join("'%s'" % n.replace("'", "\\'") for n in names)


def build_statements(params):
    rules_path = params.get("category_model") or None
    cols = _trigger_cols(rules_path)

    where = ("lower(language) = 'english' AND lower(id_source) = 'audio' "
             "AND lower(verbatimtype) = 'clientverbatim' "
             "AND %s IS NOT NULL AND length(trim(%s)) > 0" % (TEXT_FIELD, TEXT_FIELD))
    day = (params.get("classify_date") or "").strip()
    if day:
        where += " AND to_date(document_date) = '%s'" % day
    hs = (params.get("hour_start") or "").strip()
    he = (params.get("hour_end") or "").strip()
    if hs != "" and he != "":
        where += (" AND cast(substring(document_date, 12, 2) AS INT) >= %d"
                  " AND cast(substring(document_date, 12, 2) AS INT) < %d"
                  % (int(hs), int(he)))
    min_words = int(params.get("min_words") or 0)
    if min_words > 0:
        # array_remove('') so runs of >1 space don't inflate the token count.
        where += " AND size(array_remove(split(trim(%s), ' '), '')) >= %d" % (TEXT_FIELD, min_words)
    limit = int(params.get("sample_limit") or 0)
    lim = ("LIMIT %d" % limit) if limit > 0 else ""
    sample_mode = (params.get("sample_mode") or "random").strip().lower()

    conf = "true" if str(params.get("enable_confidence")).lower() == "true" else "false"
    rat = "true" if str(params.get("enable_rationales")).lower() == "true" else "false"

    # --- over-tagging gate: build the label-extraction expression -------------
    # Start from the full response array, drop labels below the confidence
    # threshold, then (optionally) keep only the top-N by confidence. The FULL
    # ungated struct is still persisted as ai_result_json below, so the cut is
    # deterministic, auditable, and re-tunable offline without re-running inference.
    try:
        thr = float(params.get("conf_threshold") or 0)
    except ValueError:
        thr = 0.0
    max_labels = int(params.get("max_labels") or 0)
    resp = "t.r.response"
    if conf == "true" and thr > 0:
        # a null score (shouldn't happen when confidence is on) counts as below cut
        resp = ("filter(%s, x -> x.confidence_score IS NOT NULL "
                "AND x.confidence_score >= %s)" % (resp, thr))
    if max_labels > 0:
        resp = ("slice(array_sort(%s, (a, b) -> "
                "CASE WHEN a.confidence_score > b.confidence_score THEN -1 "
                "WHEN a.confidence_score < b.confidence_score THEN 1 ELSE 0 END), "
                "1, %d)" % (resp, max_labels))
    labels_expr = "transform(%s, x -> x.value)" % resp

    sent = params["sentence_table"]
    tags = params["ai_tags_table"]
    scoped_tmp = tags + "__scoped_tmp"
    bysentence_tmp = tags + "__bysentence_tmp"

    s_scoped = (
        "CREATE OR REPLACE TABLE %s AS "
        "SELECT natural_id, id_document, id_verbatim, sentence_id, document_date, %s "
        "FROM %s WHERE %s" % (scoped_tmp, TEXT_FIELD, sent, where))

    # The paid step: one ai_classify() per DISTINCT sentence. v2.1 multi-label with
    # label descriptions + global instructions. The struct result is normalized via
    # to_json/from_json, then we pull the CONFIDENCE-GATED label names
    # (labels_expr, built above) into an array for the roll-up, while ai_result_json
    # keeps the full ungated response for audit / offline re-tuning. `labels` and
    # `instructions` are bound params so their JSON/quotes never touch the SQL.
    # sample_mode picks WHICH distinct sentences when capped: random (default,
    # representative) or frequency (coverage).
    if sample_mode == "frequency":
        inner = ("SELECT %s FROM %s GROUP BY %s ORDER BY count(*) DESC %s"
                 % (TEXT_FIELD, scoped_tmp, TEXT_FIELD, lim))
    else:
        inner = ("SELECT %s FROM (SELECT DISTINCT %s FROM %s) ORDER BY rand() %s"
                 % (TEXT_FIELD, TEXT_FIELD, scoped_tmp, lim))
    s_bysentence = (
        "CREATE OR REPLACE TABLE %s AS "
        "SELECT t.%s, %s AS ai_labels, "
        "t.r.error_message AS ai_error, to_json(t.r) AS ai_result_json "
        "FROM (SELECT %s, from_json(to_json(ai_classify(%s, :labels, "
        "map('version','2.1','multilabel','true','enableConfidenceScores','%s',"
        "'enableRationales','%s','instructions', :instructions))), '%s') AS r "
        "FROM (%s)) t"
        % (bysentence_tmp, TEXT_FIELD, labels_expr, TEXT_FIELD, TEXT_FIELD, conf, rat,
           _RESULT_SCHEMA, inner))

    col_exprs = []
    for cid, names in cols:
        if names:
            col_exprs.append(
                "CASE WHEN arrays_overlap(b.ai_labels, %s) THEN 1 ELSE 0 END AS `%s`"
                % (_sql_array(names), cid))
        else:
            col_exprs.append("CAST(0 AS INT) AS `%s`" % cid)

    s_final = (
        "CREATE OR REPLACE TABLE %s AS "
        "SELECT s.natural_id, s.id_document, s.id_verbatim, s.sentence_id, s.document_date, s.%s, "
        "to_json(b.ai_labels) AS ai_labels, b.ai_result_json, b.ai_error, %s "
        "FROM %s s JOIN %s b ON s.%s = b.%s"
        % (tags, TEXT_FIELD, ", ".join(col_exprs),
           scoped_tmp, bysentence_tmp, TEXT_FIELD, TEXT_FIELD))

    drops = ["DROP TABLE IF EXISTS %s" % bysentence_tmp,
             "DROP TABLE IF EXISTS %s" % scoped_tmp]
    return {"scoped": s_scoped, "bysentence": s_bysentence, "final": s_final,
            "drops": drops, "labels": build_labels_json(rules_path),
            "instructions": INSTRUCTIONS}


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
    import time

    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.sql import (
        ExecuteStatementRequestOnWaitTimeout, StatementParameterListItem)

    params = get_params()
    print("Params: %s" % params)
    wid = (params.get("warehouse_id") or "").strip()
    if not wid:
        raise ValueError("warehouse_id is required (the SQL warehouse to run the "
                         "batch ai_classify on). Pass it as a job parameter / widget.")

    st = build_statements(params)
    w = WorkspaceClient()

    def execute(sql, parameters=None, label=""):
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
        print("  %-10s done in %.1fs" % (label, time.time() - t0))
        return r

    print("Running DBSQL batch ai_classify() v2.1 on warehouse %s ..." % wid)
    execute(st["scoped"], label="scoped")
    execute(st["bysentence"],
            parameters=[StatementParameterListItem(name="labels", value=st["labels"]),
                        StatementParameterListItem(name="instructions", value=st["instructions"])],
            label="classify")
    execute(st["final"], label="fan-out")
    for d in st["drops"]:
        execute(d, label="cleanup")
    print("Wrote %s" % params["ai_tags_table"])


if __name__ == "__main__":
    run()
