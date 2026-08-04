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
        "A system that analyzes GM contact-center call transcripts at sentence "
        "grain on Databricks, using three complementary tracks — mirroring the "
        "blend Qualtrics actually documents (rules + LLM + a trained transformer): "
        "(1) a deterministic rule engine that faithfully replicates GM's Qualtrics "
        "XM Discover logic; (2) a lightweight AI-powered solution built on "
        "Databricks' native AI functions (ai_classify, ai_analyze_sentiment, "
        "ai_query embeddings, ai_gen); and (3) a trained transformer sentiment "
        "model (fine-tuned DistilBERT, 5-class, MLflow-tracked + VADER baseline)."))
    b.append(para(
        "Track 1 design decision: the matching logic is pure Python with zero "
        "dependencies, so the exact same code runs inside a Spark job at scale "
        "on Databricks and can be unit-tested locally without a cluster."))
    b.append(para(
        "Track 2 rationale: this is a lightweight AI-powered solution — "
        "Databricks AI functions provide an alternative to a custom ML model with "
        "nothing to train, host, or retrain, so GM does not own long-term model "
        "maintenance. The rule engine remains the deterministic control the AI "
        "solution is regressed against."))
    b.append(para(
        "Track 3 rationale: the one Qualtrics technique they explicitly disclose "
        "is a single transformer sentiment model (their engineering blog describes "
        "evolving from a lexicon + shallow-net hybrid to a transformer, 5-class). "
        "Research found no evidence Qualtrics uses ensemble models, and ensembles "
        "are a poor fit here (no labels; hurts determinism/explainability), so "
        "Track 3 is a single fine-tuned transformer — a model GM owns and serves "
        "at zero token cost — with a VADER lexicon baseline for the older era."))

    b.append(para("2. The four topics it tags", style="Heading1"))
    b.append(para("From two branches of GM's APM hierarchy:"))
    b.append(bullet("Contact Center → Dissatisfied → Confusing / Makes No Sense"))
    b.append(bullet("Contact Center → Dissatisfied → Inaccurate Information"))
    b.append(bullet("Loyalty → Rewards → Points"))
    b.append(bullet("Loyalty → Rewards → Points → Redeem"))

    b.append(para("3. The three tracks", style="Heading1"))
    b.append(para(
        "The POC evaluates whether Databricks can replicate and improve on GM's "
        "XM Discover tagging, so three tracks were built, from most deterministic "
        "to trained ML, plus a job that compares tracks 1 and 2."))
    b.append(table([
        ("Track", "What it is / trade-offs"),
        ("1. Rule engine  (rule_engine.py, tagger.py, voc_topic_model_job.py)",
         "Faithful re-implementation of GM's XM Discover swim-lane rules. "
         "Deterministic, explainable, zero model cost, exact control replica. "
         "Trade-off: manual rule upkeep; misses novel phrasing."),
        ("2a. AI classification  (ai_classify_job.py)",
         "An LLM assigns each sentence to a topic via ai_classify, steered by "
         "the topic's business definition; ai_analyze_sentiment adds sentiment. "
         "No keyword maintenance; handles paraphrase. Trade-off: "
         "non-deterministic; per-call cost."),
        ("2b. Topic discovery  (topic_discovery_job.py)",
         "Unsupervised: ai_query embeds sentences, KMeans (scikit-learn, "
         "serverless-safe) clusters them by meaning, ai_gen auto-names themes. "
         "Finds themes nobody defined. Trade-off: clusters need interpretation."),
        ("3. Trained transformer sentiment  (train_sentiment_model.py)",
         "Fine-tuned DistilBERT for 5-class sentiment; weak-labeled via LLM, "
         "MLflow-tracked and registered in Unity Catalog; VADER lexicon baseline. "
         "Owned, servable, zero token cost at inference. Trade-off: needs labels "
         "(bootstrapped); GPU to train well. A single transformer, not an ensemble."),
        ("Comparison  (compare_approaches_job.py)",
         "Agreement analysis: rules vs. AI per topic (overlap, rule-only, "
         "AI-only, precision/recall/F1). The regression-vs-control deliverable."),
    ]))
    b.append(para(
        "The AI classifier reads its topic definitions from the same rules.json "
        "the rule engine uses, so both topic tracks classify against identical "
        "topics.", bold=True))

    b.append(para("4. Files — rule engine (Track 1)", style="Heading1"))
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

    b.append(para("5. Files — rule-engine runners", style="Heading1"))
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

    b.append(para("6. Files — lightweight AI-powered solution (Track 2)", style="Heading1"))
    b.append(table([
        ("File", "Purpose"),
        ("ai_classify_job.py",
         "AI topic classification. Uses ai_classify (LLM assigns each sentence "
         "to a topic, steered by the topic's business definition from rules.json) "
         "and ai_analyze_sentiment. Writes voc_ai_topic_tags."),
        ("topic_discovery_job.py",
         "Unsupervised topic discovery. Embeds sentences via "
         "ai_query('databricks-gte-large-en'), clusters with scikit-learn KMeans "
         "(serverless-safe), auto-names themes with ai_gen. Writes "
         "voc_discovered_themes + voc_theme_assignments."),
        ("compare_approaches_job.py",
         "Agreement analysis between the rule tags and AI tags per topic; "
         "precision/recall/F1 with the rule engine as proxy control. "
         "Writes voc_approach_comparison."),
        ("run_ai_classify_notebook.py / run_topic_discovery_notebook.py / "
         "run_compare_notebook.py",
         "Thin Databricks notebook entrypoints for the three AI-solution jobs."),
        ("LIGHTWEIGHT_AI_SOLUTION.md",
         "Documents the three approaches, requirements (serverless + DBR 18.2+), "
         "cost notes, output tables, and caveats to verify on first real run."),
    ]))

    b.append(para("7. Files — trained sentiment model (Track 3)", style="Heading1"))
    b.append(table([
        ("File", "Purpose"),
        ("train_sentiment_model.py",
         "Weak-labels 5-class sentiment via an LLM (ai_query), fine-tunes "
         "DistilBERT (HuggingFace Trainer), logs metrics/model to MLflow and "
         "registers it in Unity Catalog. Skips + logs a stub if data is too thin."),
        ("vader_baseline.py",
         "VADER lexicon 5-class sentiment baseline — the pre-transformer era; "
         "runs locally with no GPU, for a transparent comparison point."),
        ("score_sentiment.py",
         "Batch-scores verbatims with the registered transformer + the VADER "
         "baseline; writes voc_sentiment_scored (both methods per sentence)."),
        ("run_train_sentiment_notebook.py / run_score_sentiment_notebook.py",
         "Databricks entrypoints (pip-install torch/transformers/etc, call the job)."),
        ("SENTIMENT_MODEL.md",
         "Documents the trained-model rationale (single transformer, not an "
         "ensemble), the weak-label bootstrap, requirements, and caveats."),
    ]))

    b.append(para("8. Files — outputs & validation", style="Heading1"))
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

    b.append(para("9. Files — deployment & docs", style="Heading1"))
    b.append(table([
        ("File", "Purpose"),
        ("databricks.yml",
         "Databricks Asset Bundle — defines three jobs (voc_topic_model_job for "
         "rules; voc_ai_pipeline_job for AI classify + discovery + compare; "
         "voc_sentiment_model_job for train + score) as serverless tasks. "
         "Deployed to the daria_k_sandbox workspace."),
        ("README.md", "How everything fits together and how to run it (with diagrams)."),
        ("ARCHITECTURE.md", "All Mermaid diagrams: component map + per-track runtime flows."),
        ("LIGHTWEIGHT_AI_SOLUTION.md", "Track 2 details and the three-approach comparison."),
        ("SENTIMENT_MODEL.md", "Track 3 details: trained transformer + VADER baseline."),
        ("VALIDATION_SUMMARY.md",
         "POC deliverable: what was built, what the data showed, and "
         "strengths / gaps / recommendations."),
        ("architecture_diagram.html",
         "Rendered component map + runtime flow diagrams."),
        (".gitignore",
         "Keeps generated outputs, the venv, and the client requirements docs "
         "out of git."),
    ]))

    b.append(para("10. Key findings", style="Heading1"))
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
    b.append(bullet(
        "Track caveats verified/found on real Databricks runs: ai_classify's "
        "signature is runtime-specific — labels must be a JSON string and the "
        "output is a VARIANT (cast to STRING); topic discovery uses scikit-learn "
        "KMeans because serverless forbids Spark MLlib persistence; the sentiment "
        "job must pip-install torch on serverless (no GPU there)."))
    b.append(bullet(
        "Track 3 on synthetic data: the LLM successfully weak-labeled the sample "
        "but sentiment collapses to one class (flat OnStar dialogue), so training "
        "correctly logs a skip stub. Real verbatims are needed to train for real."))

    b.append(para("11. Status", style="Heading1"))
    b.append(bullet("Track 1 (rules): built, tested (33/33), validated end-to-end locally."))
    b.append(bullet("Track 2 (lightweight AI-powered solution): ai_classify runs and writes tags on Databricks; topic-discovery + compare wired in."))
    b.append(bullet("Track 3 (trained transformer sentiment): weak-labeling runs on Databricks; train/score jobs wired in; VADER baseline runs locally."))
    b.append(bullet("All three jobs deployed to Databricks (daria_k_sandbox) as an Asset Bundle."))
    b.append(bullet("Client requirement docs scrubbed from git history; repo made private."))
    b.append(bullet("Pending: run all tracks against real, representative input data."))

    b.append(para("12. Recommended next steps", style="Heading1"))
    b.append(bullet("Load real, de-identified representative data into the POC schema and rerun all three tracks."))
    b.append(bullet("Compare the AI classifier and rule engine against GM's XM Discover control tags per node (precision/recall)."))
    b.append(bullet("Review the discovered themes with SMEs to identify emergent topics beyond the four defined nodes."))
    b.append(bullet("Adjudicate a human-labeled gold set, then retrain the Track 3 transformer on real labels (beyond LLM weak labels)."))
    b.append(bullet("Capture cost (DBU + AI-function token cost) and throughput at target volume; run the rerun repeatability benchmark."))
    b.append(bullet("Swap the sentence-ordering proxy for GM's multi-field ranking logic once provided."))

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
