#!/usr/bin/env python3
"""
make_summary_pptx.py
--------------------
Generates GM_VOC_POC_Summary.pptx — a tight, visual, outcome-focused summary of
the VOC classification POC (9 slides, real numbers from the v2 rule tags).

Uses the shared, dependency-free OOXML builder in pptx_lib.py. Run:
    python3 docs/make_summary_pptx.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pptx_lib import (  # noqa: E402
    AMBER, BLUE, CONTENT_W, CONTENT_X, CONTENT_Y, DARK, GRAY, GREEN, H,
    MARGIN, NAVY, RED, TEAL, W, WHITE, Slide, bullets, header, para, run, save_deck)


def build_deck():
    slides = []

    # 1 · Title
    s = Slide()
    s.rect(0, 0, W, H, fill=NAVY)
    s.rect(0, 0, 150000, H, fill=BLUE)
    s.rect(MARGIN, 2050000, 2400000, 34000, fill=BLUE)
    s.text(MARGIN, 2250000, W - 2 * MARGIN, 1500000, [
        para(run("Voice-of-Customer", size=4400, color=WHITE, bold=True),
             space_before=0, space_after=120),
        para(run("Classification POC", size=4400, color=WHITE, bold=True),
             space_before=0, space_after=0)])
    s.text(MARGIN, 3950000, W - 2 * MARGIN, 900000, [
        para(run("Approach, results, and what's next", size=1800, color="9DB4E0"),
             space_before=0, space_after=120),
        para(run("Databricks  ·  benchmarked against Qualtrics XM Discover",
                 size=1400, color="7C93C0"), space_before=0, space_after=0)])
    slides.append(s.xml())

    # 2 · What we did
    s = Slide()
    header(s, "Overview", "What we set out to do", 2)
    bullets(s, [
        (0, "The task:", "auto-tag every sentence in GM's call-center transcripts "
            "with business categories (e.g. advisor confusing, rewards points, "
            "redeem points)."),
        (0, "The benchmark:", "GM already does this in Qualtrics XM Discover — our "
            "source of truth to match."),
        (0, "Two approaches tested:", "a deterministic rule engine (a faithful "
            "replica of the Qualtrics rules) and AI classification (an LLM reads "
            "each sentence). Plus topic-discovery and sentiment as extras."),
        (0, "This deck:", "the outcomes — what matched, what we fixed, and the one "
            "open question on scaling AI.", NAVY),
    ])
    slides.append(s.xml())

    # 3 · Outcome: rule engine matches Qualtrics
    s = Slide()
    header(s, "Result 1", "The rule engine matches Qualtrics", 3)
    s.text(CONTENT_X, CONTENT_Y - 60000, CONTENT_W, 320000, [
        para(run("Tag volume vs. Qualtrics, per call (July 2025). Closer to 1.00 = "
                 "closer match.", size=1300, color=GRAY), space_before=0, space_after=0)])
    s.ratio_chart(CONTENT_X, CONTENT_Y + 620000, CONTENT_W, 1650000, [
        ("Confusing", 0.98, GREEN, "0.98×"),
        ("Inaccurate", 1.00, GREEN, "1.00×"),
        ("Redeem (fixed)", 1.11, GREEN, "1.11×"),
    ])
    s.table(CONTENT_X, CONTENT_Y + 2560000, CONTENT_W, [40, 20, 20, 20], [
        ["Category (calls)", "Ours", "Qualtrics", "Ratio"],
        [("CC Advisor – Confusing", {"bold": True}), "45,014", "45,789", ("0.98×", {"color": GREEN, "bold": True})],
        [("CC Advisor – Inaccurate", {"bold": True}), "4,620", "4,623", ("1.00×", {"color": GREEN, "bold": True})],
        [("Points – Redeem", {"bold": True}), "7,063", "6,358", ("1.11×", {"color": GREEN, "bold": True})],
    ], row_h=330000)
    slides.append(s.xml())

    # 4 · Outcome: Redeem fix
    s = Slide()
    header(s, "Result 2", "We found and fixed the Redeem over-count", 4)
    s.text(CONTENT_X, CONTENT_Y - 60000, CONTENT_W, 320000, [
        para(run("Redeem calls tagged, July 2025 — before vs. after the keyword fix.",
                 size=1300, color=GRAY), space_before=0, space_after=0)])
    s.hbars(CONTENT_X, CONTENT_Y + 520000, CONTENT_W, 1650000, [
        ("Before fix", 19634, RED, "19,634"),
        ("After fix", 7063, GREEN, "7,063"),
        ("Qualtrics", 6358, NAVY, "6,358"),
    ], maxval=21000)
    bullets(s, [
        (0, "Cause:", "the rule matched generic words — “use / using / used” — so "
            "“use my card” counted as a redemption."),
        (0, "Fix:", "tighten the keyword to “redeem*”. Result: 3.1× over "
            "Qualtrics → 1.1×.", GREEN),
    ], y=CONTENT_Y + 2380000, gap=False)
    slides.append(s.xml())

    # 5 · Key insight: grain
    s = Slide()
    header(s, "Key insight", "Count calls, not sentences", 5)
    s.text(CONTENT_X, CONTENT_Y - 60000, CONTENT_W, 320000, [
        para(run("“Confusing”, July 2025 — same data, counted three ways.",
                 size=1300, color=GRAY), space_before=0, space_after=0)])
    s.hbars(CONTENT_X, CONTENT_Y + 520000, CONTENT_W, 1650000, [
        ("Dashboard (sentences)", 59617, AMBER, "59,617"),
        ("Per call (fixed count)", 45014, GREEN, "45,014"),
        ("Qualtrics (calls)", 45789, NAVY, "45,789"),
    ], maxval=64000)
    bullets(s, [
        (0, "Why our numbers looked ~30% high:", "the dashboard counted sentences; "
            "Qualtrics counts calls. A call has several sentences."),
        (0, "One-line fix:", "count distinct calls (verbatims) → we match Qualtrics.",
            GREEN),
    ], y=CONTENT_Y + 2380000, gap=False)
    slides.append(s.xml())

    # 6 · AI quality
    s = Slide()
    header(s, "AI track", "AI classification: quality hinges on the model", 6)
    s.stat_card(CONTENT_X, CONTENT_Y + 120000, 3050000, 1650000,
                "14 / 5,000", "sentences tagged by Claude Sonnet 4.6 — all correct", GREEN)
    bullets(s, [
        (0, "Model is the biggest lever:", "small/cheap models over-tagged "
            "everything (“OK.” → Points). Claude Sonnet 4.6 was precise.", DARK),
        (0, "Prompt tightening:", "“most sentences match nothing” removed the "
            "remaining over-tagging."),
        (0, "So AI is viable:", "with the right model + prompt it produces "
            "trustworthy tags without hand-maintained keywords.", NAVY),
    ], x=CONTENT_X + 3350000, y=CONTENT_Y + 60000, w=CONTENT_W - 3350000, gap=True)
    slides.append(s.xml())

    # 7 · AI at scale
    s = Slide()
    header(s, "AI track", "The open question: scaling AI affordably", 7)
    s.text(CONTENT_X, CONTENT_Y - 60000, CONTENT_W, 320000, [
        para(run("Runtime on the same ~500K-sentence slice.", size=1300,
                 color=GRAY), space_before=0, space_after=0)])
    s.hbars(CONTENT_X, CONTENT_Y + 460000, CONTENT_W, 1120000, [
        ("Built-in ai_classify", 21, TEAL, "21 min"),
        ("ai_query (Sonnet 4.6)", 117, BLUE, "1 h 57 min"),
    ], maxval=132)
    bullets(s, [
        (0, "The trade-off:", "the fast path (ai_classify) uses a weaker fixed "
            "model; the accurate path (Sonnet) is rate-limited → ~7 h for a full "
            "day, and the full dataset is far larger."),
        (0, "The options:", "provisioned throughput (self-serve, open-weight "
            "models) or committed Sonnet throughput (via Databricks) — the question "
            "we're posing to Databricks.", NAVY),
    ], y=CONTENT_Y + 1780000, gap=False)
    slides.append(s.xml())

    # 8 · What worked / didn't
    s = Slide()
    header(s, "Summary", "What worked, what didn't", 8)
    half = CONTENT_W / 2 - 80000
    s.text(CONTENT_X, CONTENT_Y - 40000, half, 300000,
           [para(run("WORKED", size=1500, color=GREEN, bold=True), space_before=0, space_after=0)])
    bullets(s, [
        (0, "Rule engine", "matches Qualtrics (Confusing, Inaccurate, Redeem)."),
        (0, "Redeem + grain", "root causes found and fixed."),
        (0, "AI with Sonnet 4.6", "precise, no keyword upkeep."),
        (0, "Batch SQL inference", "the fast way to run AI at scale."),
    ], x=CONTENT_X, y=CONTENT_Y + 300000, w=half, gap=False)
    s.text(CONTENT_X + CONTENT_W / 2 + 80000, CONTENT_Y - 40000, half, 300000,
           [para(run("STILL OPEN / LIMITED", size=1500, color=RED, bold=True),
                 space_before=0, space_after=0)])
    bullets(s, [
        (0, "Built-in ai_classify", "fast, multi-label (v2.1), but fixed model."),
        (0, "Small models", "over-tag everything."),
        (0, "Pay-per-token limits", "make full-scale AI slow/costly."),
        (0, "Scaling AI", "needs provisioned/committed throughput."),
    ], x=CONTENT_X + CONTENT_W / 2 + 80000, y=CONTENT_Y + 300000, w=half, gap=False)
    slides.append(s.xml())

    # 9 · Recommendations
    s = Slide()
    header(s, "Where we land", "Recommendations & next steps", 9)
    bullets(s, [
        (0, "Ship the rule engine:", "a production-ready, faithful, explainable "
            "replica. Count distinct calls on the dashboard; deploy the Redeem fix "
            "(now in the v2 table).", GREEN),
        (0, "Scale AI with better throughput:", "provisioned or committed throughput "
            "to keep Sonnet-level quality without the runtime wall."),
        (0, "Use AI as a complement:", "great for novel phrasing and zero keyword "
            "upkeep — always validated against the rule engine."),
        (0, "Next:", "run both classifiers on a shared sample and produce a "
            "per-category precision/recall comparison vs. Qualtrics.", NAVY),
    ])
    slides.append(s.xml())

    return slides


def main():
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "GM_VOC_POC_Summary.pptx")
    save_deck(build_deck(), out)


if __name__ == "__main__":
    main()
