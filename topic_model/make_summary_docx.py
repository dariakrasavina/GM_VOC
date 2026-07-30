"""
make_summary_docx.py
--------------------
Generates a Word (.docx) project summary for the GM VOC POC, using only the
Python standard library (a .docx is a zip of XML parts). Produces:

    GM_VOC_POC_Summary.docx

Supports headings (H1/H2), body paragraphs, bullets, and simple 2-column tables.
"""
import os
import zipfile
from xml.sax.saxutils import escape

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "GM_VOC_POC_Summary.docx")

# ---------------------------------------------------------------------------
# Minimal docx package parts.
# ---------------------------------------------------------------------------
CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="xml" ContentType="application/xml"/>
 <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
 <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""

RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
 <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>
  <w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/></w:rPr></w:style>
 <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>
  <w:pPr><w:spacing w:after="120"/></w:pPr>
  <w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:b/><w:sz w:val="48"/><w:color w:val="1F3864"/></w:rPr></w:style>
 <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>
  <w:pPr><w:spacing w:before="240" w:after="80"/></w:pPr>
  <w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:b/><w:sz w:val="30"/><w:color w:val="1F6FEB"/></w:rPr></w:style>
 <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>
  <w:pPr><w:spacing w:before="160" w:after="60"/></w:pPr>
  <w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:b/><w:sz w:val="24"/><w:color w:val="333333"/></w:rPr></w:style>
</w:styles>"""


def _runs(text, bold=False):
    b = "<w:b/>" if bold else ""
    return ('<w:r><w:rPr>%s</w:rPr><w:t xml:space="preserve">%s</w:t></w:r>'
            % (b, escape(text)))


def para(text, style=None, bold=False):
    ppr = '<w:pPr><w:pStyle w:val="%s"/></w:pPr>' % style if style else ""
    return "<w:p>%s%s</w:p>" % (ppr, _runs(text, bold))


def bullet(text):
    return ('<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="0"/></w:numPr>'
            '<w:ind w:left="360" w:hanging="180"/></w:pPr>'
            '%s%s</w:p>' % (_runs("•  "), _runs(text)))


def table(rows):
    """rows: list of (col1, col2) tuples; first row treated as header."""
    def cell(txt, bold=False, shade=None):
        sh = ('<w:shd w:val="clear" w:fill="%s"/>' % shade) if shade else ""
        return ('<w:tc><w:tcPr><w:tcW w:w="4600" w:type="dxa"/>%s</w:tcPr>%s</w:tc>'
                % (sh, para_no_p(txt, bold)))

    def para_no_p(txt, bold):
        return "<w:p>%s</w:p>" % _runs(txt, bold)

    out = ['<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/>'
           '<w:tblW w:w="9200" w:type="dxa"/>'
           '<w:tblBorders>'
           '<w:top w:val="single" w:sz="4" w:color="CCCCCC"/>'
           '<w:left w:val="single" w:sz="4" w:color="CCCCCC"/>'
           '<w:bottom w:val="single" w:sz="4" w:color="CCCCCC"/>'
           '<w:right w:val="single" w:sz="4" w:color="CCCCCC"/>'
           '<w:insideH w:val="single" w:sz="4" w:color="CCCCCC"/>'
           '<w:insideV w:val="single" w:sz="4" w:color="CCCCCC"/>'
           '</w:tblBorders></w:tblPr>']
    for i, (c1, c2) in enumerate(rows):
        hdr = (i == 0)
        shade = "DDE7F5" if hdr else None
        out.append("<w:tr>%s%s</w:tr>" % (cell(c1, hdr, shade), cell(c2, hdr, shade)))
    out.append("</w:tbl>")
    return "".join(out)


# ---------------------------------------------------------------------------
# Document content.
# ---------------------------------------------------------------------------
def build_body():
    b = []
    b.append(para("GM Voice of Customer POC", style="Title"))
    b.append(para("Databricks Topic-Model Replication of Qualtrics XM Discover — "
                  "build summary and file reference", bold=True))
    b.append(para(""))

    b.append(para("1. What it is", style="Heading1"))
    b.append(para(
        "A system that replicates GM's Qualtrics XM Discover topic-tagging on "
        "Databricks. It takes contact-center call transcripts sentence by "
        "sentence, applies GM's existing rule-based topic model, and outputs "
        "which topics each sentence matches — so Databricks results can be "
        "validated 1:1 against the current Qualtrics control set."))
    b.append(para(
        "Core design decision: the matching logic is pure Python with zero "
        "dependencies, so the exact same code runs inside a Spark job at scale "
        "on Databricks and can be unit-tested locally without a cluster."))

    b.append(para("2. The four topics it tags", style="Heading1"))
    b.append(para("From two branches of GM's APM hierarchy:"))
    b.append(bullet("Contact Center → Dissatisfied → Confusing / Makes No Sense"))
    b.append(bullet("Contact Center → Dissatisfied → Inaccurate Information"))
    b.append(bullet("Loyalty → Rewards → Points"))
    b.append(bullet("Loyalty → Rewards → Points → Redeem"))

    b.append(para("3. Files — core engine", style="Heading1"))
    b.append(table([
        ("File", "Purpose"),
        ("rule_engine.py",
         "Implements GM's XM Discover query language from scratch: parser + "
         "evaluator for OR/AND/NOT, quoted phrases, wildcards (* ?), fuzzy (~), "
         "proximity (\"a b\"~3), and attribute searches. No pyspark import — "
         "that is what makes it portable."),
        ("tagger.py",
         "Shared tagging core. Applies the global scope filter (English + audio "
         "+ customer-side) then the four topic nodes to one sentence. Imported "
         "by both the local runner and the Spark job, guaranteeing identical logic."),
        ("rules.json",
         "The topic hierarchy + all lane rules, expressed as data. This is what "
         "the engine reads."),
        ("build_rules_config.py",
         "Generates rules.json by parsing GM's source Excel workbook, so the "
         "encoded rules provably match GM's originals rather than being "
         "hand-transcribed."),
    ]))

    b.append(para("4. Files — runners", style="Heading1"))
    b.append(table([
        ("File", "Purpose"),
        ("voc_topic_model_job.py",
         "The production Databricks/PySpark job. Reads the two Delta tables, "
         "joins on natural_id, applies the rules via a vectorized pandas_udf, "
         "and writes the tagged output + frequency tables. Table names and "
         "dates are parameterized."),
        ("run_job_notebook.py",
         "Thin notebook entrypoint the deployed Databricks job runs — imports "
         "the job module and calls run()."),
        ("run_local.py",
         "Stdlib-only local driver. Runs the same tagging over CSV files with "
         "no cluster needed — used to validate everything locally."),
    ]))

    b.append(para("5. Files — outputs & validation", style="Heading1"))
    b.append(table([
        ("File", "Purpose"),
        ("build_dashboard.py",
         "Renders a self-contained HTML review dashboard: topic frequencies and "
         "representative verbatims with matched-term “chicklets”."),
        ("test_rule_engine.py",
         "33 unit tests covering every documented syntax rule. All passing."),
        ("make_demo_fixture.py",
         "Builds schema-identical demo data with realistic verbatims — needed "
         "because the provided sample data is synthetic OnStar dialogue with no "
         "topical content to tag."),
    ]))

    b.append(para("6. Files — deployment & docs", style="Heading1"))
    b.append(table([
        ("File", "Purpose"),
        ("databricks.yml",
         "Databricks Asset Bundle — defines the job as a serverless task and "
         "deploys it. Deployed to the daria_k_sandbox workspace."),
        ("README.md", "How everything fits together and how to run it."),
        ("VALIDATION_SUMMARY.md",
         "POC deliverable: what was built, what the data showed, and "
         "strengths / gaps / recommendations."),
        (".gitignore",
         "Keeps generated outputs, the venv, and the client requirements docs "
         "out of git."),
    ]))

    b.append(para("7. Key findings", style="Heading1"))
    b.append(bullet(
        "The provided sample data is synthetic (OnStar roadside dialogue, ~40 "
        "distinct sentences, zero topic vocabulary), so a real run correctly "
        "tags 0 sentences. Tagging accuracy cannot be benchmarked until real "
        "representative data lands."))
    b.append(bullet(
        "The demo fixture proves the engine works: it correctly handles the "
        "hard cases — dealer exclusion, negation, agent-side filtering, and "
        "“he wants to redeem his points” tagging Points but not Redeem."))
    b.append(bullet(
        "Data join verified: sentence.natural_id == metadata.natural_id (1:1 on "
        "the sample). The global scope filter passes ~43% of the sample "
        "(English + audio + customer-side)."))

    b.append(para("8. Status", style="Heading1"))
    b.append(bullet("Built, tested (33/33), validated end-to-end locally."))
    b.append(bullet("Deployed to Databricks (daria_k_sandbox) as an Asset Bundle."))
    b.append(bullet("Client requirement docs scrubbed from git history; repo made private."))
    b.append(bullet("Pending: final force-push of the cleaned code to GitHub."))

    b.append(para("9. Recommended next steps", style="Heading1"))
    b.append(bullet("Load real, de-identified representative data into the POC schema and rerun the job."))
    b.append(bullet("Join the Databricks tags to the XM Discover control tags per node for a precision/recall comparison."))
    b.append(bullet("Capture cost (DBU) and throughput at target volume for the scaling story; run the rerun-after-rule-change repeatability benchmark."))
    b.append(bullet("Swap the sentence-ordering proxy for Andrew's multi-field ranking logic once provided."))

    return "".join(b)


def main():
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>%s'
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/></w:sectPr>'
        '</w:body></w:document>' % build_body())

    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        z.writestr("word/_rels/document.xml.rels", DOC_RELS)
        z.writestr("word/styles.xml", STYLES)
        z.writestr("word/document.xml", document)
    print("Wrote %s" % OUT)


if __name__ == "__main__":
    main()
