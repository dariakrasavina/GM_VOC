#!/usr/bin/env python3
"""
make_deepdive_pptx.py
---------------------
Generates GM_VOC_Technical_Deep_Dive.pptx — a diagram-heavy technical walkthrough
of the three classification implementations: the rule engine (file by file), the
built-in ai_classify path, and the ai_query_sql batch path (full logic).

Uses the shared, dependency-free OOXML builder in pptx_lib.py. Run:
    python3 docs/make_deepdive_pptx.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pptx_lib import (  # noqa: E402
    AMBER, BLUE, CARD, CONTENT_W, CONTENT_X, CONTENT_Y, DARK, GRAY, GREEN, H,
    LIGHT, LINE, MARGIN, NAVY, PURPLE, RED, TEAL, W, WHITE, Slide, bullets,
    caption, header, para, run, save_deck)


# ---------------------------------------------------------------- flow helpers
def hflow(s, x, y, bw, bh, gap, boxes, arrow=GRAY):
    """Horizontal row of boxes joined by right arrows. boxes: (title, sub, fill, accent)."""
    xs = []
    for i, (t, sub, fill, acc) in enumerate(boxes):
        bx = x + i * (bw + gap)
        xs.append(bx)
        lines = [(t, 1200, DARK, True)] + ([(sub, 1000, GRAY, False)] if sub else [])
        s.box(bx, y, bw, bh, lines, fill=fill, accent=acc)
        if i > 0:
            s.connector(xs[i - 1] + bw, y + bh / 2, bx, y + bh / 2, color=arrow)
    return xs


def vflow(s, x, y, bw, bh, gap, boxes, arrow=BLUE):
    """Vertical stack of boxes joined by down arrows."""
    ys = []
    for i, (t, sub, fill, acc) in enumerate(boxes):
        by = y + i * (bh + gap)
        ys.append(by)
        lines = [(t, 1250, DARK, True)] + ([(sub, 1050, GRAY, False)] if sub else [])
        s.box(x, by, bw, bh, lines, fill=fill, accent=acc)
        if i > 0:
            s.connector(x + bw / 2, ys[i - 1] + bh, x + bw / 2, by, color=arrow)
    return ys


# ---------------------------------------------------------------- deck
def build_deck():
    slides = []

    # 1 · Title
    s = Slide()
    s.rect(0, 0, W, H, fill=NAVY)
    s.rect(0, 0, 150000, H, fill=BLUE)
    s.rect(MARGIN, 2050000, 2400000, 34000, fill=BLUE)
    s.text(MARGIN, 2250000, W - 2 * MARGIN, 1500000, [
        para(run("Classification —", size=4200, color=WHITE, bold=True),
             space_before=0, space_after=120),
        para(run("Technical Deep Dive", size=4200, color=WHITE, bold=True),
             space_before=0, space_after=0)])
    s.text(MARGIN, 3900000, W - 2 * MARGIN, 900000, [
        para(run("How the rule engine, ai_classify, and ai_query_sql work",
                 size=1700, color="9DB4E0"), space_before=0, space_after=120),
        para(run("End to end, file by file  ·  GM Voice-of-Customer POC  ·  Databricks",
                 size=1300, color="7C93C0"), space_before=0, space_after=0)])
    slides.append(s.xml())

    # 2 · Three approaches at a glance
    s = Slide()
    header(s, "Orientation", "Three classifiers, one output shape", 2)
    caption(s, CONTENT_Y - 40000, "All three read the SAME shared/category_model.json, "
            "roll leaf matches up to parents, and write one 0/1 column per category — "
            "so they compare like-for-like.")
    s.table(CONTENT_X, CONTENT_Y + 560000, CONTENT_W, [20, 22, 16, 16, 26], [
        ["Approach", "Function", "Labels", "Model", "Output table"],
        [("Rule engine", {"bold": True}), "keyword / boolean rules", "multi + roll-up", "none (deterministic)", ("voc_classification_rule_tags", {"mono": True, "size": 950})],
        [("ai_classify", {"bold": True}), "built-in ai_classify() v2.1", "multi + confidence", "fixed managed", ("voc_classification_ai_classify_tags", {"mono": True, "size": 950})],
        [("ai_query_sql", {"bold": True}), "ai_query() + prompt", "multi + roll-up", "your choice (Sonnet 4.6)", ("voc_classification_ai_query_sql_tags", {"mono": True, "size": 950})],
    ], row_h=620000)
    caption(s, CONTENT_Y + 3050000, "Rule engine = the deterministic control. "
            "ai_query_sql = the scalable AI path. ai_classify = a different method "
            "(one best label, fixed model) worth comparing.")
    slides.append(s.xml())

    # 3 · Rule engine — what it is
    s = Slide()
    header(s, "Rule engine · 1", "What it is", 3)
    bullets(s, [
        (0, "A from-scratch implementation", "of GM's XM Discover Designer query "
            "language — the exact rules GM uses today, run on Databricks."),
        (0, "Deterministic:", "same sentence → same tags, every run. No model, no "
            "drift, no per-call cost."),
        (0, "Explainable:", "every tag records the matched terms (“chicklets”) so a "
            "reviewer sees WHY a sentence was tagged."),
        (0, "One code path:", "rule_engine.py + tagger.py never import pyspark, so "
            "the identical logic runs on a laptop (unit-tested) and at scale in Spark."),
        (0, "Role:", "the CONTROL the AI approaches are measured against.", GREEN),
    ])
    slides.append(s.xml())

    # 4 · Rule engine — architecture (diagram)
    s = Slide()
    header(s, "Rule engine · 2", "Architecture — data, engine, consumers", 4)
    top_y = CONTENT_Y + 120000
    bw, bh = 3180000, 1080000
    xs = hflow(s, CONTENT_X, top_y, bw, bh, 690000, [
        ("category_model.json", "the rules (data)", CARD, NAVY),
        ("rule_engine.py", "parse → AST → evaluate", CARD, BLUE),
        ("tagger.py", "scope + all categories + roll-up", CARD, TEAL),
    ])
    # tagger.py fans out to three consumers
    cons_y = top_y + bh + 900000
    cbw, cbh = 3180000, 980000
    cons_x = [CONTENT_X, CONTENT_X + (cbw + 690000), CONTENT_X + 2 * (cbw + 690000)]
    consumers = [
        ("run_local.py", "laptop run over CSVs", LIGHT, GRAY),
        ("voc_topic_model_job.py", "FULL Spark job (pandas_udf)", LIGHT, GREEN),
        ("incremental_rule_job.py", "incremental re-tag on rule change", LIGHT, AMBER),
    ]
    tagger_cx = xs[2] + bw / 2
    for cx, (t, sub, fill, acc) in zip(cons_x, consumers):
        s.box(cx, cons_y, cbw, cbh, [(t, 1200, DARK, True), (sub, 1000, GRAY, False)],
              fill=fill, accent=acc)
        s.connector(tagger_cx, top_y + bh, cx + cbw / 2, cons_y, color=TEAL)
    caption(s, cons_y + cbh + 180000, "The rules are DATA. rule_engine.py evaluates "
            "one rule; tagger.py applies the whole model (scope + roll-up); three "
            "consumers run it locally, at full scale, or incrementally.")
    slides.append(s.xml())

    # 5 · Rule engine — how one rule matches (diagram)
    s = Slide()
    header(s, "Rule engine · 3", "How one category rule matches (4 lanes)", 5)
    caption(s, CONTENT_Y - 40000, "Each category is up to four “swim lanes”. A "
            "sentence matches when:  keywords AND and AND and2 AND (NOT not).")
    ly = CONTENT_Y + 640000
    lbw, lbh = 2500000, 1000000
    lanes = [
        ("keywords", "OR seed — required", CARD, GREEN),
        ("and", "extra required terms", CARD, BLUE),
        ("and2", "e.g. NOT agent-side", CARD, BLUE),
        ("not", "exclusions", CARD, RED),
    ]
    lxs = hflow(s, CONTENT_X, ly, lbw, lbh, 300000, lanes, arrow=WHITE)  # no visible arrows
    gate_x = CONTENT_X + CONTENT_W / 2 - 1500000
    gate_y = ly + lbh + 760000
    s.box(gate_x, gate_y, 3000000, 820000,
          [("AND  →  MATCH  (+ matched terms)", 1350, WHITE, True)],
          fill=NAVY, border=NAVY, accent=None)
    for lx, (_, _, _, acc) in zip(lxs, lanes):
        s.connector(lx + lbw / 2, ly + lbh, gate_x + 1500000, gate_y, color=acc)
    caption(s, gate_y + 900000, "Lane strings are parsed to an AST (Or / And / Not / "
            "PhraseTerm / AttrTerm). Supported: wildcards (room*), fuzzy (word~), "
            "proximity (\"a b\"~3), exact phrases, and attribute predicates "
            "(_id_source:audio, language:english).")
    slides.append(s.xml())

    # 6 · Rule engine — file by file (table)
    s = Slide()
    header(s, "Rule engine · 4", "File by file", 6)
    s.table(CONTENT_X, CONTENT_Y - 40000, CONTENT_W, [30, 70], [
        ["File", "What it does"],
        [("rule_engine.py", {"mono": True, "bold": True, "size": 1050}),
         "Lexer + recursive-descent parser → AST; matchers for word / phrase / wildcard / fuzzy / proximity / attribute; CompiledNode.evaluate combines the 4 lanes."],
        [("tagger.py", {"mono": True, "bold": True, "size": 1050}),
         "Loads category_model.json; global scope filter (in_scope); tag() = multi-label + ancestor roll-up. Pure Python."],
        [("voc_topic_model_job.py", {"mono": True, "bold": True, "size": 1050}),
         "FULL PySpark job: SQL scope pre-filter → join metadata → pandas_udf(tagger.tag) → explode to 0/1 + __terms columns → write tags + frequencies."],
        [("incremental_rule_job.py", {"mono": True, "bold": True, "size": 1050}),
         "Incremental re-tag after a rule change (diff snapshot → affected columns/rows → MERGE in place). See slide 8."],
        [("run_local.py", {"mono": True, "bold": True, "size": 1050}),
         "Stdlib laptop runner over the sample CSVs — same tagger, no cluster."],
        [("test_rule_engine.py", {"mono": True, "bold": True, "size": 1050}),
         "46 unit tests: every rule operator + the incremental planning logic."],
    ], row_h=560000)
    slides.append(s.xml())

    # 7 · Rule engine — full job pipeline (diagram)
    s = Slide()
    header(s, "Rule engine · 5", "The full job, end to end", 7)
    py = CONTENT_Y + 260000
    pbw, pbh = 1930000, 1450000
    hflow(s, CONTENT_X, py, pbw, pbh, 310000, [
        ("Scope pre-filter", "SQL: EN + audio + customer + date", LIGHT, BLUE),
        ("Join metadata", "on natural_id", CARD, BLUE),
        ("pandas_udf", "tagger.tag() per row", CARD, GREEN),
        ("Explode", "0/1 + __terms per category (in-scope)", CARD, TEAL),
        ("Write", "rule_tags + frequencies", LIGHT, NAVY),
    ])
    caption(s, py + pbh + 320000, "The SQL scope pre-filter is the big win: the slow "
            "per-row Python matching (pandas_udf) only ever sees in-scope rows, not "
            "the whole corpus. Output is one row per sentence, one 0/1 column per "
            "category (leaves + rolled-up parents) + the terms that fired.")
    slides.append(s.xml())

    # 8 · Rule engine — incremental (diagram)
    s = Slide()
    header(s, "Rule engine · 6", "Incremental re-tag (no full re-run)", 8)
    vx = CONTENT_X
    vbw, vbh, vg = 4650000, 700000, 300000
    ys = vflow(s, vx, CONTENT_Y - 20000, vbw, vbh, vg, [
        ("Diff rules vs __rules_snapshot", "changed / added / removed categories", CARD, NAVY),
        ("Affected columns", "changed cats + their ancestors (roll-up)", CARD, BLUE),
        ("Candidate rows", "currently-tagged ∪ new-keyword pre-filter", CARD, TEAL),
        ("Recompute on candidates", "same tagger, affected columns only", CARD, GREEN),
        ("MERGE into tags in place", "refresh frequencies + snapshot", LIGHT, GREEN),
    ])
    # side annotations
    ax = vx + vbw + 520000
    aw = CONTENT_W - vbw - 520000
    s.box(ax, CONTENT_Y - 20000, aw, 1500000, [
        ("Why it's safe", 1300, DARK, True),
        ("A tag is a deterministic function of text + rule. Unchanged rules can't "
         "change a column; a changed rule can only flip sentences that match the "
         "OLD or NEW keywords.", 1100, GRAY, False)], fill=WHITE, accent=BLUE, anchor="t", align="l")
    s.box(ax, CONTENT_Y + 1680000, aw, 1500000, [
        ("Guardrails", 1300, DARK, True),
        ("Global scope-filter change → abort (scope moves for every row). Fuzzy / "
         "attribute keyword seeds a text pre-filter can't bound → that one column "
         "falls back to a full in-scope scan.", 1100, GRAY, False)],
        fill=WHITE, accent=AMBER, anchor="t", align="l")
    slides.append(s.xml())

    # 9 · ai_classify — what it is
    s = Slide()
    header(s, "ai_classify", "Purpose-built classifier (v2.1)", 9)
    bullets(s, [
        (0, "The function:", "ai_classify(text, labels, MAP('version','2.1', …)) — "
            "native Databricks SQL, no prompt to write."),
        (0, "Labels with descriptions:", "labels are a JSON object {category: "
            "definition}, so the model sees each category's business definition "
            "(no cryptic ≤50-char labels)."),
        (0, "Multi-label:", "'multilabel'='true' returns ALL applicable categories; "
            "nothing matches → empty list (no synthetic “None of the above”). "
            "Matches the rule engine + Qualtrics.", GREEN),
        (0, "Confidence + rationale:", "0–1 confidence per label for triage; "
            "rationales optional (off in prod — extra output tokens)."),
        (0, "Still a FIXED managed model:", "you CANNOT choose Claude/GPT — so its "
            "quality ceiling is capped vs. the Sonnet ai_query path.", RED),
    ])
    slides.append(s.xml())

    # 10 · ai_classify — pipeline (diagram, 3 statements)
    s = Slide()
    header(s, "ai_classify", "Pipeline — three batch SQL statements", 10)
    vx, vbw, vbh, vg = CONTENT_X, CONTENT_W, 1080000, 320000
    y0 = CONTENT_Y - 40000
    steps = [
        ("1 · scoped_tmp", "CREATE TABLE … the in-scope rows for the day "
         "(English, audio, customer-side)", LIGHT, BLUE),
        ("2 · bysentence_tmp   — the paid step", "per DISTINCT sentence (frequency-first, "
         "sample_limit):  ai_classify(words, :labels, MAP('version','2.1','multilabel','true',"
         "…)) → matched label names + confidence", CARD, GREEN),
        ("3 · tags table", "fan labels to all rows;  column = arrays_overlap(ai_labels, "
         "array(trigger_names)) → roll up;  then DROP the temps", LIGHT, NAVY),
    ]
    ys = []
    for i, (t, sub, fill, acc) in enumerate(steps):
        by = y0 + i * (vbh + vg)
        ys.append(by)
        s.box(vx, by, vbw, vbh, [(t, 1300, DARK, True), (sub, 1120, GRAY, False, True)],
              fill=fill, accent=acc, anchor="ctr", align="l")
        if i > 0:
            s.connector(vx + vbw / 2, ys[i - 1] + vbh, vx + vbw / 2, by, color=BLUE)
    caption(s, y0 + 3 * (vbh + vg) + 40000, "Dedup: identical sentences are classified "
            "once (you pay per UNIQUE sentence), then the label fans out to every row "
            "that shares the text.")
    slides.append(s.xml())

    # 11 · ai_query_sql — what it is
    s = Slide()
    header(s, "ai_query_sql · 1", "Custom-prompt, multi-label, batch", 11)
    bullets(s, [
        (0, "The function:", "ai_query(endpoint, prompt + sentence) → a JSON array of "
            "ALL applicable categories (multi-label), steered by each category's "
            "definition in the prompt."),
        (0, "Set-based BATCH SQL:", "CREATE TABLE AS SELECT ai_query(...) on a SQL "
            "warehouse via the Statement Execution API — the engine drives high "
            "concurrency to the endpoint (vs. PySpark's partition-bound crawl).", BLUE),
        (0, "Dedup:", "classify each DISTINCT sentence once, fan the tags to every row "
            "that shares it."),
        (0, "Roll-up compiled to SQL:", "arrays_overlap over precomputed per-category "
            "trigger names — no UDF (slide 14)."),
        (0, "Your choice of model:", "default databricks-claude-sonnet-4-6 (best "
            "quality tested). Two modes: dedup or context (slide 13).", GREEN),
    ])
    slides.append(s.xml())

    # 12 · ai_query_sql — full pipeline (diagram)
    s = Slide()
    header(s, "ai_query_sql · 2", "The full logic — three statements", 12)
    vx, vbw, vbh, vg = CONTENT_X, CONTENT_W, 1090000, 300000
    y0 = CONTENT_Y - 40000
    steps = [
        ("1 · scoped_tmp", "in-scope rows for the day  (EN + audio + customer-side, "
         "optional hour window, optional min_words filter)", LIGHT, BLUE),
        ("2 · bysentence_tmp   — the ONLY paid, model-bound step",
         "SELECT words, from_json( ai_query(endpoint, concat(:prompt, words)) )  AS "
         "ai_categories   FROM (distinct sentences, frequency-first / random, "
         "sample_limit)", CARD, GREEN),
        ("3 · tags table",
         "join back to ALL scoped rows;  per category:  arrays_overlap(ai_categories, "
         "array(trigger_names)) → 0/1;  then DROP the temps", LIGHT, NAVY),
    ]
    ys = []
    for i, (t, sub, fill, acc) in enumerate(steps):
        by = y0 + i * (vbh + vg)
        ys.append(by)
        s.box(vx, by, vbw, vbh, [(t, 1250, DARK, True), (sub, 1080, GRAY, False, True)],
              fill=fill, accent=acc, anchor="ctr", align="l")
        if i > 0:
            s.connector(vx + vbw / 2, ys[i - 1] + vbh, vx + vbw / 2, by, color=BLUE)
    caption(s, y0 + 3 * (vbh + vg) + 30000, "The prompt is passed as a bound :prompt "
            "parameter so its quotes/newlines never touch the SQL text. Serverless-safe: "
            "temp tables materialize each step exactly once (no persist/cache).")
    slides.append(s.xml())

    # 13 · ai_query_sql — dedup vs context
    s = Slide()
    header(s, "ai_query_sql · 3", "Two modes: dedup vs. context", 13)
    colw = CONTENT_W / 2 - 180000
    lx, rx = CONTENT_X, CONTENT_X + CONTENT_W / 2 + 180000
    by, bh2 = CONTENT_Y + 40000, 2650000
    s.box(lx, by, colw, bh2, [
        ("DEDUP   (context_window = 0)", 1350, WHITE, True)],
        fill=TEAL, border=TEAL, anchor="t", align="l")
    bullets(s, [
        (0, "Classify each DISTINCT sentence once", "then fan the tag to all rows "
            "sharing it.", DARK),
        (0, "Cheapest", "— pay per unique sentence."),
        (0, "Trade-off:", "the sentence is judged in isolation."),
        (0, "Full-day default", "(~1.25M distinct sentences)."),
    ], x=lx + 40000, y=by + 620000, w=colw - 80000, gap=False, size0=1200, size1=1100)
    s.box(rx, by, colw, bh2, [
        ("CONTEXT   (context_window > 0)", 1350, WHITE, True)],
        fill=PURPLE, border=PURPLE, anchor="t", align="l")
    bullets(s, [
        (0, "Classify each ROW with ±N neighbors", "from the same call (LAG/LEAD), "
            "wrapped in >>> <<< markers.", DARK),
        (0, "No dedup", "→ one call per row (more calls)."),
        (0, "Better on ambiguous", "short sentences."),
        (0, "For quality-eval samples,", "not full-day scale."),
    ], x=rx + 40000, y=by + 620000, w=colw - 80000, gap=False, size0=1200, size1=1100)
    slides.append(s.xml())

    # 14 · ai_query_sql — roll-up compiled to SQL (diagram)
    s = Slide()
    header(s, "ai_query_sql · 4", "Hierarchy roll-up, compiled to SQL", 14)
    caption(s, CONTENT_Y - 40000, "The LLM returns leaf category NAMES. Each category "
            "column turns on when the returned array overlaps its trigger set — "
            "itself (if a target) plus any target descendants. Pure SQL, no UDF.")
    y0 = CONTENT_Y + 640000
    s.box(CONTENT_X, y0, 3550000, 1000000, [
        ("ai_query returns", 1150, GRAY, False),
        ('["Points - Redeem"]', 1200, DARK, True, True)], fill=CARD, accent=GREEN, align="l")
    mid_x = CONTENT_X + 3550000 + 620000
    s.box(mid_x, y0, 3350000, 1000000, [
        ("arrays_overlap(", 1150, GRAY, False),
        ("  ai_categories, array(names))", 1150, DARK, True, True)], fill=CARD, accent=BLUE, align="l")
    s.connector(CONTENT_X + 3550000, y0 + 500000, mid_x, y0 + 500000, color=GRAY)
    # rolled-up columns
    roll_x = mid_x + 3350000 + 620000
    s.box(roll_x, y0 - 260000, CONTENT_W - (roll_x - CONTENT_X), 1520000, [
        ("sets these columns = 1", 1150, GRAY, False),
        ("points_redeem", 1150, DARK, True, True),
        ("loyalty_rewards_points", 1150, DARK, True, True),
        ("loyalty_rewards   ·   loyalty", 1150, DARK, True, True)],
        fill=LIGHT, accent=NAVY, align="l", anchor="ctr")
    s.connector(mid_x + 3350000, y0 + 500000, roll_x, y0 + 500000, color=GRAY)
    caption(s, y0 + 1500000, "So a single leaf hit (Points - Redeem) rolls up to its "
            "parents — matching how the rule engine and Qualtrics count parent "
            "categories.")
    slides.append(s.xml())

    # 15 · ai_query_sql — the prompt
    s = Slide()
    header(s, "ai_query_sql · 5", "The prompt (build_prompt in ai_common.py)", 15)
    s.box(CONTENT_X, CONTENT_Y, CONTENT_W, 2900000, [
        ("build_prompt()  — key instructions", 1300, NAVY, True),
        ("", 600, GRAY, False),
        ("• “Assign a category ONLY when the sentence CLEARLY expresses it. MOST "
         "sentences match nothing → return [].”", 1180, DARK, False),
        ("• Lists each category name + its business definition.", 1180, DARK, False),
        ("• “Do NOT tag Confusing just because the customer asks a question — only "
         "when they explicitly say the advisor/info was confusing.”", 1180, DARK, False),
        ("• If context markers are shown, classify only the sentence between >>> and <<<.", 1180, DARK, False),
        ("• Ends with:  “Reply with ONLY a JSON array … If none apply, reply []. Text: ”", 1180, DARK, False, True),
    ], fill=WHITE, accent=GREEN, anchor="t", align="l")
    caption(s, CONTENT_Y + 3020000, "Tightening these rules (especially “most sentences "
            "match nothing”) is what stopped the model over-tagging short filler.")
    slides.append(s.xml())

    # 16 · Execution & throughput
    s = Slide()
    header(s, "Cross-cutting", "Execution, scale & serverless notes", 16)
    bullets(s, [
        (0, "Statement Execution API:", "submit the CTAS, poll until SUCCEEDED "
            "(wait_timeout + CONTINUE) — how the DBSQL batch jobs drive the warehouse."),
        (0, "Batch beats per-partition:", "set-based ai_query lets the engine manage "
            "concurrency to the endpoint; the PySpark per-partition path is bounded by "
            "partition count and crawls on big days."),
        (0, "Throughput ceiling:", "pay-per-token Sonnet ≈ 2,900 distinct/min; the "
            "ai_classify managed model ≈ 4,800/min. A full day (~1.25M distinct) is "
            "hours → use PROVISIONED / committed throughput at full scale.", AMBER),
        (0, "Serverless-safe:", "no .persist()/.cache() (forbidden on serverless) — "
            "each step materializes to a temp Delta table instead."),
    ])
    slides.append(s.xml())

    # 17 · When to use which
    s = Slide()
    header(s, "Summary", "When to use which", 17)
    s.table(CONTENT_X, CONTENT_Y, CONTENT_W, [22, 48, 30], [
        ["Approach", "Strengths", "Use it for"],
        [("Rule engine", {"bold": True}), "Deterministic, explainable, free, incremental updates", ("Production control / source-of-truth match", {"color": GREEN, "bold": True})],
        [("ai_query_sql", {"bold": True}), "Best AI quality, multi-label, scalable batch, choose the model", ("The recommended AI path", {"color": BLUE, "bold": True})],
        [("ai_classify", {"bold": True}), "Simplest, multi-label v2.1 + confidence, but fixed model (no choice)", ("Comparison / quick reads only", {"color": AMBER, "bold": True})],
    ], row_h=680000)
    caption(s, CONTENT_Y + 2450000, "All three share category_model.json and the same "
            "output shape, so the compare job scores them against each other and "
            "against Qualtrics on identical ground.")
    slides.append(s.xml())

    return slides


def main():
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "GM_VOC_Technical_Deep_Dive.pptx")
    save_deck(build_deck(), out)


if __name__ == "__main__":
    main()
