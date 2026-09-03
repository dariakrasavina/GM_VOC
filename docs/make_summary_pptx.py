#!/usr/bin/env python3
"""
make_summary_pptx.py
--------------------
Generates GM_VOC_POC_Summary.pptx — a tight, visual, outcome-focused summary of
the VOC classification POC (9 slides, real numbers from the v2 rule tags).

No third-party libraries: a .pptx is an OOXML zip, built here with the stdlib
(zipfile + hand-written DrawingML). Charts are drawn as native shapes so they
render in PowerPoint, Keynote and Google Slides. Run:
    python3 docs/make_summary_pptx.py
"""
import os
import zipfile
from xml.dom.minidom import parseString

# ---------------------------------------------------------------- geometry
W = 12192000            # 13.333 in  (16:9)
H = 6858000             # 7.5 in
MARGIN = 640080
BAND_H = 1150000
CONTENT_X = MARGIN
CONTENT_Y = 1420000
CONTENT_W = W - 2 * MARGIN

# ---------------------------------------------------------------- palette
NAVY, BLUE, TEAL = "1B2A4A", "2E6FF2", "0E7C7B"
GREEN, RED, AMBER = "2E9E5B", "C0392B", "B9770E"
DARK, GRAY = "223047", "5A6473"
LIGHT, CARD, WHITE, LINE = "E7EDF6", "F5F7FB", "FFFFFF", "D5DEEA"
FONT = "Calibri"


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def run(text, size=1600, color=DARK, bold=False, italic=False):
    b = ' b="1"' if bold else ""
    i = ' i="1"' if italic else ""
    return ('<a:r><a:rPr lang="en-US" sz="%d"%s%s dirty="0">'
            '<a:solidFill><a:srgbClr val="%s"/></a:solidFill>'
            '<a:latin typeface="%s"/></a:rPr><a:t>%s</a:t></a:r>'
            % (size, b, i, color, FONT, esc(text)))


def para(runs_xml, algn="l", level=0, bullet=None, space_before=200,
         space_after=400, line_pct=None):
    marL, indent, buf = 0, 0, "<a:buNone/>"
    if bullet is not None:
        marL = 274320 * (level + 1)
        indent = -274320
        buf = '<a:buFont typeface="Arial"/><a:buChar char="%s"/>' % esc(bullet)
    ln = ('<a:lnSpc><a:spcPct val="%d"/></a:lnSpc>' % line_pct) if line_pct else ""
    pPr = ('<a:pPr marL="%d" indent="%d" algn="%s">%s'
           '<a:spcBef><a:spcPts val="%d"/></a:spcBef>'
           '<a:spcAft><a:spcPts val="%d"/></a:spcAft>%s</a:pPr>'
           % (marL, indent, algn, ln, space_before, space_after, buf))
    return "<a:p>%s%s</a:p>" % (pPr, runs_xml)


class Slide:
    def __init__(self):
        self._id = 1
        self.shapes = []

    def nid(self):
        self._id += 1
        return self._id

    def rect(self, x, y, w, h, fill=None, line=None, line_w=9525, prst="rect"):
        x, y, w, h = int(x), int(y), int(w), int(h)
        f = ('<a:solidFill><a:srgbClr val="%s"/></a:solidFill>' % fill) if fill else "<a:noFill/>"
        l = ('<a:ln w="%d"><a:solidFill><a:srgbClr val="%s"/></a:solidFill></a:ln>'
             % (line_w, line)) if line else '<a:ln><a:noFill/></a:ln>'
        self.shapes.append(
            '<p:sp><p:nvSpPr><p:cNvPr id="%d" name="r%d"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
            '<p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
            '<a:prstGeom prst="%s"><a:avLst/></a:prstGeom>%s%s</p:spPr>'
            '<p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>'
            % (self.nid(), self._id, x, y, w, h, prst, f, l))

    def text(self, x, y, w, h, paras, anchor="t", fill=None, wrap=True):
        x, y, w, h = int(x), int(y), int(w), int(h)
        f = ('<a:solidFill><a:srgbClr val="%s"/></a:solidFill>' % fill) if fill else "<a:noFill/>"
        wr = 'square' if wrap else 'none'
        body = ('<a:bodyPr wrap="%s" lIns="36000" tIns="18000" rIns="36000" '
                'bIns="18000" anchor="%s"><a:normAutofit/></a:bodyPr>' % (wr, anchor))
        self.shapes.append(
            '<p:sp><p:nvSpPr><p:cNvPr id="%d" name="t%d"/><p:cNvSpPr>'
            '<a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr/></p:nvSpPr>'
            '<p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>%s'
            '<a:ln><a:noFill/></a:ln></p:spPr>'
            '<p:txBody>%s<a:lstStyle/>%s</p:txBody></p:sp>'
            % (self.nid(), self._id, x, y, w, h, f, body, "".join(paras)))

    def table(self, x, y, w, col_w, rows, header_fill=NAVY, row_h=340000):
        total = sum(col_w)
        col_w = [int(c * w / total) for c in col_w]
        col_w[-1] += int(w) - sum(col_w)
        grid = "".join('<a:gridCol w="%d"/>' % c for c in col_w)
        tr_xml = []
        for ri, row in enumerate(rows):
            tcs = []
            for ci, cell in enumerate(row):
                txt, opts = (cell if isinstance(cell, tuple) else (cell, {}))
                head = ri == 0
                fill = opts.get("fill", header_fill if head else (CARD if ri % 2 else WHITE))
                color = opts.get("color", WHITE if head else DARK)
                bold = opts.get("bold", head)
                size = opts.get("size", 1250 if head else 1200)
                algn = opts.get("algn", "l" if ci == 0 else "ctr")
                p = para(run(txt, size=size, color=color, bold=bold), algn=algn,
                         space_before=0, space_after=0)
                tcs.append('<a:tc><a:txBody><a:bodyPr/><a:lstStyle/>%s</a:txBody>'
                           '<a:tcPr marL="73152" marR="73152" marT="18000" '
                           'marB="18000" anchor="ctr"><a:solidFill>'
                           '<a:srgbClr val="%s"/></a:solidFill></a:tcPr></a:tc>'
                           % (p, fill))
            h = int(row_h * (1.1 if ri == 0 else 1.0))
            tr_xml.append('<a:tr h="%d">%s</a:tr>' % (h, "".join(tcs)))
        self.shapes.append(
            '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="%d" name="tbl%d"/>'
            '<p:cNvGraphicFramePr><a:graphicFrameLocks noGrp="1"/>'
            '</p:cNvGraphicFramePr><p:nvPr/></p:nvGraphicFramePr>'
            '<p:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></p:xfrm>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/'
            'drawingml/2006/table"><a:tbl><a:tblPr firstRow="1" bandRow="1"/>'
            '<a:tblGrid>%s</a:tblGrid>%s</a:tbl></a:graphicData></a:graphic>'
            '</p:graphicFrame>'
            % (self.nid(), self._id, int(x), int(y), int(w), len(rows) * row_h,
               grid, "".join(tr_xml)))

    # ---- charts (drawn from shapes) --------------------------------------
    def hbars(self, x, y, w, h, bars, maxval=None):
        """bars: list of (label, value, color, value_text)."""
        n = len(bars)
        labw, valw = int(w * 0.30), int(w * 0.16)
        bx, bw = x + labw, w - labw - valw
        mx = maxval or max(b[1] for b in bars) * 1.08
        band = h / n
        barh = band * 0.5
        for i, (label, val, color, vtext) in enumerate(bars):
            cy = y + i * band + (band - barh) / 2
            self.text(x, y + i * band, labw - 40000, band,
                      [para(run(label, size=1300, color=DARK, bold=True),
                            algn="l", space_before=0, space_after=0)], anchor="ctr")
            self.rect(bx, cy, bw, barh, fill=LIGHT)
            wv = max(int(bw * val / mx), 12700)
            self.rect(bx, cy, wv, barh, fill=color)
            self.text(bx + wv + 36000, y + i * band, valw, band,
                      [para(run(vtext, size=1350, color=color, bold=True),
                            algn="l", space_before=0, space_after=0)], anchor="ctr")

    def ratio_chart(self, x, y, w, h, rows, axis_max=1.4):
        """rows: list of (label, ratio, color, endlabel). Vertical ref line at 1.0."""
        n = len(rows)
        labw = int(w * 0.26)
        bx, bw = x + labw, int(w * 0.56)
        band = h / n
        barh = band * 0.5
        refx = bx + int(bw * 1.0 / axis_max)
        self.rect(refx, y - 40000, 15875, band * n + 80000, fill=NAVY)
        self.text(refx - 700000, y - 340000, 1400000, 300000,
                  [para(run("Qualtrics = 1.00", size=1050, color=NAVY, bold=True),
                        algn="ctr", space_before=0, space_after=0)])
        for i, (label, ratio, color, endlabel) in enumerate(rows):
            cy = y + i * band + (band - barh) / 2
            self.text(x, y + i * band, labw - 30000, band,
                      [para(run(label, size=1300, color=DARK, bold=True),
                            algn="l", space_before=0, space_after=0)], anchor="ctr")
            self.rect(bx, cy, bw, barh, fill=LIGHT)
            self.rect(bx, cy, int(bw * ratio / axis_max), barh, fill=color)
            self.text(bx + int(bw * ratio / axis_max) + 36000, y + i * band,
                      int(w * 0.30), band,
                      [para(run(endlabel, size=1300, color=color, bold=True),
                            algn="l", space_before=0, space_after=0)], anchor="ctr")

    def stat_card(self, x, y, w, h, big, label, color):
        self.rect(x, y, w, h, fill=CARD, line=LINE)
        self.rect(x, y, w, 82550, fill=color)
        self.text(x, y + h * 0.16, w, h * 0.44,
                  [para(run(big, size=3400, color=color, bold=True), algn="ctr",
                        space_before=0, space_after=0)], anchor="ctr")
        self.text(x + 90000, y + h * 0.60, w - 180000, h * 0.36,
                  [para(run(label, size=1250, color=DARK), algn="ctr",
                        space_before=0, space_after=0)], anchor="t")

    def xml(self):
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
                'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
                '<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/>'
                '<p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr>'
                '<a:xfrm><a:off x="0" y="0"/><a:ext cx="%d" cy="%d"/>'
                '<a:chOff x="0" y="0"/><a:chExt cx="%d" cy="%d"/></a:xfrm>'
                '</p:grpSpPr>%s</p:spTree></p:cSld>'
                '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'
                % (W, H, W, H, "".join(self.shapes)))


def header(s, kicker, title, num):
    s.rect(0, 0, W, BAND_H, fill=NAVY)
    s.rect(0, BAND_H, W, 38100, fill=BLUE)
    if kicker:
        s.text(MARGIN, 165000, CONTENT_W, 300000,
               [para(run(kicker.upper(), size=1150, color="9DB4E0", bold=True),
                     space_before=0, space_after=0)])
    s.text(MARGIN, 430000, CONTENT_W, 620000,
           [para(run(title, size=2600, color=WHITE, bold=True),
                 space_before=0, space_after=0)], anchor="ctr")
    s.text(MARGIN, H - 360000, CONTENT_W - 500000, 300000,
           [para(run("GM Voice-of-Customer Classification POC  ·  Databricks",
                     size=900, color=GRAY), space_before=0, space_after=0)])
    s.text(W - MARGIN - 500000, H - 360000, 500000, 300000,
           [para(run(str(num), size=1000, color=GRAY, bold=True), algn="r",
                 space_before=0, space_after=0)])


def caption(s, y, text):
    s.text(CONTENT_X, y, CONTENT_W, 560000,
           [para(run(text, size=1300, color=GRAY), space_before=0,
                 space_after=0, line_pct=104000)])


def bullets(s, items, x=CONTENT_X, y=CONTENT_Y, w=CONTENT_W, h=None, gap=True):
    if h is None:
        h = H - y - 500000
    paras = []
    for level, lead, rest, *rc in items:
        color = rc[0] if rc else (BLUE if level == 0 else TEAL)
        r = ""
        if lead:
            r += run(lead, size=1600 if level == 0 else 1400, color=color, bold=True)
        if rest:
            r += run((" " if lead else "") + rest, size=1600 if level == 0 else 1400,
                     color=DARK if level == 0 else GRAY)
        paras.append(para(r, level=level, bullet="▪" if level == 0 else "–",
                          space_before=460 if (gap and level == 0) else 110,
                          space_after=110, line_pct=104000))
    s.text(x, y, w, h, paras)


# ---------------------------------------------------------------- deck
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
        (0, "Built-in ai_classify", "fast but too weak & single-label."),
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


# ---------------------------------------------------------------- OOXML shell
def theme_xml():
    cs = ('<a:clrScheme name="VOC"><a:dk1><a:srgbClr val="1B2A4A"/></a:dk1>'
          '<a:lt1><a:srgbClr val="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="223047"/></a:dk2>'
          '<a:lt2><a:srgbClr val="E7EDF6"/></a:lt2><a:accent1><a:srgbClr val="2E6FF2"/></a:accent1>'
          '<a:accent2><a:srgbClr val="0E7C7B"/></a:accent2><a:accent3><a:srgbClr val="2E9E5B"/></a:accent3>'
          '<a:accent4><a:srgbClr val="B9770E"/></a:accent4><a:accent5><a:srgbClr val="C0392B"/></a:accent5>'
          '<a:accent6><a:srgbClr val="5A6473"/></a:accent6><a:hlink><a:srgbClr val="2E6FF2"/></a:hlink>'
          '<a:folHlink><a:srgbClr val="0E7C7B"/></a:folHlink></a:clrScheme>')
    fs = ('<a:fontScheme name="VOC"><a:majorFont><a:latin typeface="Calibri"/>'
          '<a:ea typeface=""/><a:cs typeface=""/></a:majorFont><a:minorFont>'
          '<a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/>'
          '</a:minorFont></a:fontScheme>')
    ph = '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    fmt = ('<a:fmtScheme name="VOC"><a:fillStyleLst>%s%s%s</a:fillStyleLst>'
           '<a:lnStyleLst><a:ln w="6350">%s</a:ln><a:ln w="12700">%s</a:ln>'
           '<a:ln w="19050">%s</a:ln></a:lnStyleLst><a:effectStyleLst>'
           '<a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle>'
           '<a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/>'
           '</a:effectStyle></a:effectStyleLst><a:bgFillStyleLst>%s%s%s'
           '</a:bgFillStyleLst></a:fmtScheme>' % (ph, ph, ph, ph, ph, ph, ph, ph, ph))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'name="VOC"><a:themeElements>%s%s%s</a:themeElements><a:objectDefaults/>'
            '<a:extraClrSchemeLst/></a:theme>' % (cs, fs, fmt))


def master_xml():
    tx = ('<p:txStyles><p:titleStyle><a:lvl1pPr><a:defRPr sz="2800">'
          '<a:solidFill><a:schemeClr val="tx1"/></a:solidFill>'
          '<a:latin typeface="Calibri"/></a:defRPr></a:lvl1pPr></p:titleStyle>'
          '<p:bodyStyle><a:lvl1pPr><a:defRPr sz="1600"><a:solidFill>'
          '<a:schemeClr val="tx1"/></a:solidFill><a:latin typeface="Calibri"/>'
          '</a:defRPr></a:lvl1pPr></p:bodyStyle><p:otherStyle><a:lvl1pPr>'
          '<a:defRPr sz="1400"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill>'
          '<a:latin typeface="Calibri"/></a:defRPr></a:lvl1pPr></p:otherStyle>'
          '</p:txStyles>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            '<p:cSld><p:bg><p:bgPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill>'
            '<a:effectLst/></p:bgPr></p:bg><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/>'
            '<p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/>'
            '<a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm>'
            '</p:grpSpPr></p:spTree></p:cSld><p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" '
            'tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" '
            'accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" '
            'folHlink="folHlink"/><p:sldLayoutIdLst><p:sldLayoutId id="2147483649" '
            'r:id="rId1"/></p:sldLayoutIdLst>%s</p:sldMaster>' % tx)


def layout_xml():
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'type="blank" preserve="1"><p:cSld name="Blank"><p:spTree><p:nvGrpSpPr>'
            '<p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr>'
            '<a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/>'
            '<a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld>'
            '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>')


def presentation_xml(n):
    ids = "".join('<p:sldId id="%d" r:id="rId%d"/>' % (256 + i, 2 + i) for i in range(n))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'saveSubsetFonts="1"><p:sldMasterIdLst><p:sldMasterId id="2147483648" '
            'r:id="rId1"/></p:sldMasterIdLst><p:sldIdLst>%s</p:sldIdLst>'
            '<p:sldSz cx="%d" cy="%d"/><p:notesSz cx="6858000" cy="9144000"/>'
            '</p:presentation>' % (ids, W, H))


def _rels(items):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships">%s</Relationships>' % "".join(items))


def presentation_rels(n):
    R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
    rels = ['<Relationship Id="rId1" Type="%sslideMaster" '
            'Target="slideMasters/slideMaster1.xml"/>' % R]
    for i in range(n):
        rels.append('<Relationship Id="rId%d" Type="%sslide" '
                    'Target="slides/slide%d.xml"/>' % (2 + i, R, i + 1))
    rels.append('<Relationship Id="rId%d" Type="%spresProps" Target="presProps.xml"/>'
                % (2 + n, R))
    rels.append('<Relationship Id="rId%d" Type="%stheme" Target="theme/theme1.xml"/>'
                % (3 + n, R))
    return _rels(rels)


def content_types(n):
    P = "application/vnd.openxmlformats-officedocument.presentationml."
    ov = ['<Override PartName="/ppt/presentation.xml" ContentType="%spresentation.main+xml"/>' % P,
          '<Override PartName="/ppt/presProps.xml" ContentType="%spresProps+xml"/>' % P,
          '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="%sslideMaster+xml"/>' % P,
          '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="%sslideLayout+xml"/>' % P,
          '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/'
          'vnd.openxmlformats-officedocument.theme+xml"/>']
    for i in range(n):
        ov.append('<Override PartName="/ppt/slides/slide%d.xml" ContentType="%sslide+xml"/>'
                   % (i + 1, P))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-'
            'package.relationships+xml"/><Default Extension="xml" ContentType='
            '"application/xml"/>%s</Types>' % "".join(ov))


def main():
    slides = build_deck()
    n = len(slides)
    R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
    parts = {
        "[Content_Types].xml": content_types(n),
        "_rels/.rels": _rels(['<Relationship Id="rId1" Type="%sofficeDocument" '
                              'Target="ppt/presentation.xml"/>' % R]),
        "ppt/presentation.xml": presentation_xml(n),
        "ppt/_rels/presentation.xml.rels": presentation_rels(n),
        "ppt/presProps.xml": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:presentationPr xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>'),
        "ppt/theme/theme1.xml": theme_xml(),
        "ppt/slideMasters/slideMaster1.xml": master_xml(),
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": _rels([
            '<Relationship Id="rId1" Type="%sslideLayout" Target="../slideLayouts/slideLayout1.xml"/>' % R,
            '<Relationship Id="rId2" Type="%stheme" Target="../theme/theme1.xml"/>' % R]),
        "ppt/slideLayouts/slideLayout1.xml": layout_xml(),
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": _rels([
            '<Relationship Id="rId1" Type="%sslideMaster" Target="../slideMasters/slideMaster1.xml"/>' % R]),
    }
    for i, sx in enumerate(slides):
        parts["ppt/slides/slide%d.xml" % (i + 1)] = sx
        parts["ppt/slides/_rels/slide%d.xml.rels" % (i + 1)] = _rels([
            '<Relationship Id="rId1" Type="%sslideLayout" Target="../slideLayouts/slideLayout1.xml"/>' % R])

    for name, xml in parts.items():
        try:
            parseString(xml)
        except Exception as e:
            raise SystemExit("Malformed XML in %s: %s" % (name, e))

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "GM_VOC_POC_Summary.pptx")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, xml in parts.items():
            z.writestr(name, xml)
    print("Wrote %s  (%d slides, %d parts)" % (out, n, len(parts)))


if __name__ == "__main__":
    main()
