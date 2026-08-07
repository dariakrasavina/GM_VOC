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
    """Render (label, description) rows as a definition list.

    Hand-authored Word tables need an explicit <w:tblGrid>/fixed layout or Word
    collapses columns to one character wide. A definition list (bold term +
    indented description paragraph) renders reliably everywhere and reads well
    for these label→purpose pairs. The first row is treated as a header and
    skipped (the surrounding section heading already labels the list).
    """
    out = []
    for i, (c1, c2) in enumerate(rows):
        if i == 0:
            continue  # header row — redundant with the section heading
        # Bold term.
        out.append("<w:p><w:pPr><w:spacing w:before=\"80\" w:after=\"0\"/></w:pPr>"
                    "%s</w:p>" % _runs(c1, bold=True))
        # Indented description.
        out.append("<w:p><w:pPr><w:ind w:left=\"360\"/>"
                   "<w:spacing w:after=\"40\"/></w:pPr>%s</w:p>" % _runs(c2))
    return "".join(out)


# ---------------------------------------------------------------------------
# Document content.
# ---------------------------------------------------------------------------
def build_body():
    b = []
    b.append(para("GM Voice of Customer POC", style="Title"))
    b.append(para("Databricks build summary and file reference", bold=True))
    b.append(para(""))

    b.append(para("1. What it is", style="Heading1"))
    b.append(para(
        "A system that analyzes GM contact-center call transcripts on Databricks, "
        "to see whether it can match GM's current Qualtrics XM Discover setup. "
        "Everything works at the sentence level: each sentence gets one or more "
        "categories, and optionally a sentiment."))
    b.append(para(
        "Terms (as XM Discover uses them): a category model holds categories; "
        "classification is the process of assigning sentences to categories; "
        "topic modeling discovers new themes from the data; sentiment analysis "
        "is a separate, parallel layer (how the customer feels)."))
    b.append(para(
        "There are four tracks. Two do the same job — classification into the "
        "category model — so they can be compared: (1) Classification (rule "
        "engine), the deterministic keyword rules GM uses today; and (2) "
        "Classification (ai_classify), an LLM that assigns categories. A third, "
        "(3) Topic modeling, discovers themes nobody predefined. A fourth, (4) "
        "the Sentiment model, is complementary and used alongside the winner."))
    b.append(para(
        "Rule-engine design note: the matching logic is pure Python with zero "
        "dependencies, so the exact same code runs at scale on Databricks and "
        "can be unit-tested locally without a cluster."))
    b.append(para(
        "Sentiment model note: a single trained transformer (fine-tuned "
        "DistilBERT, 5-class) with a simple word-list (VADER) baseline. We use "
        "one model, not an ensemble, because there are no labeled examples yet "
        "and a single model is easier to explain and more repeatable — the two "
        "things GM values. Registered in MLflow so GM would own and run it cheaply."))

    b.append(para("2. The four categories", style="Heading1"))
    b.append(para("From two branches of GM's category hierarchy:"))
    b.append(bullet("Contact Center → Dissatisfied → Confusing / Makes No Sense"))
    b.append(bullet("Contact Center → Dissatisfied → Inaccurate Information"))
    b.append(bullet("Loyalty → Rewards → Points"))
    b.append(bullet("Loyalty → Rewards → Points → Redeem"))

    b.append(para("3. The tracks", style="Heading1"))
    b.append(para(
        "Two tracks do the same job (classification) and are compared so GM can "
        "pick the best; one does topic modeling (discovery); one does sentiment "
        "(complementary)."))
    b.append(table([
        ("Track", "What it is / trade-offs"),
        ("Classification (rule engine)",
         "Applies GM's category rules (keyword / phrase / boolean). "
         "Deterministic, explainable, zero model cost. "
         "Trade-off: manual rule upkeep; misses novel phrasing."),
        ("Classification (ai_classify)",
         "An LLM assigns categories via the ai_classify function. "
         "No keyword maintenance; handles paraphrase. Trade-off: "
         "non-deterministic; per-call cost."),
        ("Topic modeling",
         "Discovery: ai_query embeds sentences, KMeans (scikit-learn, "
         "serverless-safe) clusters them by meaning, ai_gen auto-names themes. "
         "Finds themes nobody predefined. Trade-off: themes need interpretation."),
        ("Sentiment model",
         "Fine-tuned DistilBERT for 5-class sentiment; MLflow-tracked and "
         "registered in Unity Catalog; VADER word-list baseline. Owned, servable, "
         "zero token cost. Trade-off: needs labels (bootstrapped for now)."),
        ("Comparison",
         "Agreement analysis: rule engine vs. ai_classify per category (overlap, "
         "rule-only, AI-only, precision/recall/F1)."),
    ]))
    b.append(para(
        "Both classifiers read the same category_model.json, so they classify "
        "against identical categories.", bold=True))

    b.append(para("4. Files — Classification (rule engine)", style="Heading1"))
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
        ("category_model.json",
         "The topic hierarchy + all lane rules, expressed as data. This is what "
         "the engine reads."),
        ("build_category_model.py",
         "Generates category_model.json by parsing GM's source Excel workbook, so the "
         "encoded rules provably match GM's originals rather than being "
         "hand-transcribed."),
    ]))

    b.append(para("5. Files — rule-engine runners", style="Heading1"))
    b.append(table([
        ("File", "Purpose"),
        ("voc_classification_rule_job.py",
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

    b.append(para("6. Files — Classification (ai_classify) + Topic modeling", style="Heading1"))
    b.append(table([
        ("File", "Purpose"),
        ("ai_classify_job.py",
         "AI topic classification. Uses ai_classify (LLM assigns each sentence "
         "to a topic, steered by the topic's business definition from category_model.json) "
         "and ai_analyze_sentiment. Writes voc_classification_ai_tags."),
        ("topic_discovery_job.py",
         "Unsupervised topic discovery. Embeds sentences via "
         "ai_query('databricks-gte-large-en'), clusters with scikit-learn KMeans "
         "(serverless-safe), auto-names themes with ai_gen. Writes "
         "voc_topicmodeling_themes + voc_topicmodeling_assignments."),
        ("compare_approaches_job.py",
         "Agreement analysis between the rule tags and AI tags per category; "
         "precision/recall/F1 with the rule engine as proxy control. "
         "Writes voc_classification_comparison."),
        ("run_ai_classify_notebook.py / run_topic_discovery_notebook.py / "
         "run_compare_notebook.py",
         "Thin Databricks notebook entrypoints for the three AI-solution jobs."),
        ("LIGHTWEIGHT_AI_SOLUTION.md",
         "Documents the three approaches, requirements (serverless + DBR 18.2+), "
         "cost notes, output tables, and caveats to verify on first real run."),
    ]))

    b.append(para("7. Files — Sentiment model", style="Heading1"))
    b.append(table([
        ("File", "Purpose"),
        ("train_sentiment_model.py",
         "Weak-labels 5-class sentiment via an LLM (ai_query), fine-tunes "
         "DistilBERT (HuggingFace Trainer), logs metrics/model to MLflow and "
         "registers it in Unity Catalog. Skips + logs a stub if data is too thin."),
        ("vader_baseline.py",
         "VADER word-list 5-class sentiment baseline; "
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
         "Renders a self-contained HTML review dashboard: category frequencies and "
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
         "Databricks Asset Bundle — defines three jobs (voc_classification_rule_job for "
         "rules; voc_ai_pipeline_job for AI classify + discovery + compare; "
         "voc_sentiment_model_job for train + score) as serverless tasks. "
         "Deployed to the daria_k_sandbox workspace."),
        ("README.md", "How everything fits together and how to run it (with diagrams)."),
        ("ARCHITECTURE.md", "All Mermaid diagrams: component map + per-track runtime flows."),
        ("LIGHTWEIGHT_AI_SOLUTION.md", "Classification (ai_classify) + topic modeling details."),
        ("SENTIMENT_MODEL.md", "Sentiment model details: trained transformer + VADER baseline."),
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
        "distinct sentences, none of the category vocabulary), so a real run "
        "correctly assigns 0 categories. Accuracy cannot be benchmarked until "
        "real representative data lands."))
    b.append(bullet(
        "The demo fixture proves the rule engine works: it correctly handles the "
        "hard cases — dealer exclusion, negation, agent-side filtering, and "
        "“he wants to redeem his points” assigning Points but not Redeem."))
    b.append(bullet(
        "Data join verified: sentence.natural_id == metadata.natural_id (1:1 on "
        "the sample). The scope filter passes ~43% of the sample "
        "(English + audio + customer-side)."))
    b.append(bullet(
        "Runtime lessons from real Databricks runs: the ai_classify function's "
        "signature is runtime-specific — labels must be a JSON string and the "
        "output is a VARIANT (cast to STRING); topic modeling uses scikit-learn "
        "KMeans because serverless forbids Spark MLlib persistence; the sentiment "
        "job must pip-install torch on serverless (no GPU there)."))
    b.append(bullet(
        "Sentiment on synthetic data: the LLM successfully bootstrapped labels "
        "but sentiment collapses to one class (flat OnStar dialogue), so training "
        "correctly logs a skip. Real transcripts are needed to train for real."))

    b.append(para("11. Status", style="Heading1"))
    b.append(bullet("Classification (rule engine): built, 33/33 unit tests pass, validated locally."))
    b.append(bullet("Classification (ai_classify): runs and writes tags on Databricks."))
    b.append(bullet("Topic modeling: runs on Databricks; compare job wired in (standalone)."))
    b.append(bullet("Sentiment model: train/score jobs run on Databricks; VADER baseline runs locally."))
    b.append(bullet("All jobs deployed to Databricks (daria_k_sandbox) as an Asset Bundle."))
    b.append(bullet("Client requirement docs scrubbed from git history; repo made private."))
    b.append(bullet("Pending: run all tracks against real, representative input data."))

    b.append(para("12. Recommended next steps", style="Heading1"))
    b.append(bullet("Load real, de-identified representative data into the POC schema and rerun all tracks."))
    b.append(bullet("Compare the two classifiers against GM's existing category tags (precision/recall)."))
    b.append(bullet("Review the topic-modeling themes with the team to find emergent topics beyond the four categories."))
    b.append(bullet("Label a set of real examples, then retrain the sentiment model on them (beyond bootstrapped labels)."))
    b.append(bullet("Capture cost (DBU + AI-function token cost) and throughput at target volume."))
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
