"""
train_sentiment_model.py
------------------------
Track 3 — a TRAINED transformer sentiment model for the GM VOC POC, mirroring the
approach Qualtrics documents for Text iQ / XM Discover: a single transformer-based
deep-learning model producing 5-class sentiment (Very Positive ... Very Negative),
having evolved from an older lexicon + shallow-net hybrid.
(Ref: https://www.qualtrics.com/news/sentiment-analysis-with-text-iq/)

WHY THIS IS A SEPARATE TRACK
  - Track 1 (rule engine) and Track 2 (AI functions) replicate topic tagging.
  - This track is the one Qualtrics technique not yet built: a *trained*,
    servable, zero-token-cost sentiment model that GM would own via MLflow /
    Unity Catalog Model Registry (contrast with the pay-per-call
    ai_analyze_sentiment function).
  - Research showed ENSEMBLE models are a poor fit here (no labels, hurts
    determinism/explainability); a single fine-tuned transformer is the sound,
    Qualtrics-faithful choice.

THE LABEL PROBLEM (important, honest)
  Supervised training needs labels; GM has none yet. Faithful to the literature,
  we BOOTSTRAP weak labels with a zero-shot LLM (ai_query), fine-tune the
  transformer on those, and treat this as a starting model to be refined once
  humans adjudicate a gold set. This is documented as weak supervision, not
  ground truth. A VADER lexicon baseline (see vader_baseline.py) mirrors the
  pre-transformer era for comparison.

PIPELINE
  1. Scope + sample in-scope customer verbatims.
  2. Weak-label each with a 5-class sentiment via ai_query (zero-shot LLM).
  3. Fine-tune DistilBERT (HuggingFace Trainer) on the weak labels.
  4. Evaluate (accuracy / macro-F1) on a held-out split.
  5. Log params, metrics, and the model to MLflow; register in Unity Catalog.

REQUIREMENTS (cannot run in a plain local env — needs Databricks ML runtime):
  - Databricks Runtime for ML (GPU strongly recommended for fine-tuning).
  - Libraries: transformers, datasets, torch, evaluate, mlflow (all on DBR ML).
  - ai_query weak-labeling needs serverless/Model-Serving access + a chat endpoint.
  - On the synthetic sample data the model will be degenerate (no real signal);
    the deliverable here is the METHOD + MLflow train->log->register lifecycle,
    to be validated on real verbatims later.
"""
import os
import sys

# Catalog/schema used to build fully-qualified table names for standalone/local
# runs. In production the Databricks bundle passes full table names as job
# parameters (built from bundle variables), which override everything below.
CATALOG = "daria_krasavina"
SCHEMA = "gm_voc"
_NS = "%s.%s" % (CATALOG, SCHEMA)

DEFAULTS = {
    "sentence_table": _NS + ".qualtrics_audio_transcripts_sentence_level_sample_data",
    "date_start": "2025-07-01",
    "date_end": "2026-06-30",
    # Weak-labeling LLM endpoint (zero-shot). Pay-per-token — keep sample small.
    "label_endpoint": "databricks-meta-llama-3-3-70b-instruct",
    "sample_limit": "300",           # rows to weak-label + train on (POC-bounded)
    # Base transformer to fine-tune.
    "base_model": "distilbert-base-uncased",
    "num_train_epochs": "3",
    "batch_size": "16",
    "test_size": "0.2",
    # MLflow / Unity Catalog registration target.
    "experiment_path": "/Shared/gm_voc_sentiment",
    "registered_model": _NS + ".voc_sentiment_transformer",
    "weak_labels_table": _NS + ".voc_sentiment_weak_labels",
}

TEXT_FIELD = "words"
HERE = os.path.dirname(os.path.abspath(__file__))

# The 5-class scheme Qualtrics documents (Very Positive ... Very Negative).
LABELS = ["Very Negative", "Negative", "Neutral", "Positive", "Very Positive"]
LABEL2ID = {l: i for i, l in enumerate(LABELS)}
ID2LABEL = {i: l for i, l in enumerate(LABELS)}


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


def weak_label_dataframe(spark, params):
    """Return a Spark DF of (words, label_str) using a zero-shot LLM.

    ai_query is prompted to return exactly one of the five class names. We keep
    the prompt strict and parse defensively; unrecognized outputs are dropped.
    """
    from pyspark.sql import functions as F

    limit = int(params.get("sample_limit") or 0)
    limit_clause = ("LIMIT %d" % limit) if limit > 0 else ""
    scoped_sql = """
        SELECT natural_id, id_verbatim, {text} AS words
        FROM {sent}
        WHERE lower(language) = 'english'
          AND lower(id_source) = 'audio'
          AND lower(verbatimtype) = 'clientverbatim'
          AND to_date(document_date) BETWEEN '{ds}' AND '{de}'
          AND {text} IS NOT NULL AND length(trim({text})) > 2
        {limit}
    """.format(text=TEXT_FIELD, sent=params["sentence_table"],
               ds=params["date_start"], de=params["date_end"], limit=limit_clause)
    scoped = spark.sql(scoped_sql)

    prompt = (
        "Classify the sentiment of this customer sentence into exactly one of: "
        "Very Negative, Negative, Neutral, Positive, Very Positive. "
        "Reply with only the label. Sentence: ")
    labeled = (scoped
        .withColumn("_prompt", F.lit(prompt))
        .withColumn("_raw", F.expr(
            "ai_query('%s', concat(_prompt, words))" % params["label_endpoint"]))
        # Normalize: take the first matching known label from the model output.
        .withColumn("label_str", F.expr(
            "CASE "
            "WHEN _raw ILIKE '%very negative%' THEN 'Very Negative' "
            "WHEN _raw ILIKE '%very positive%' THEN 'Very Positive' "
            "WHEN _raw ILIKE '%negative%' THEN 'Negative' "
            "WHEN _raw ILIKE '%positive%' THEN 'Positive' "
            "WHEN _raw ILIKE '%neutral%' THEN 'Neutral' "
            "ELSE NULL END"))
        .filter(F.col("label_str").isNotNull())
        .select("natural_id", "id_verbatim", "words", "label_str"))
    return labeled


def run():
    import mlflow
    import numpy as np
    from pyspark.sql import SparkSession

    spark = SparkSession.builder.appName("gm_voc_sentiment_train").getOrCreate()
    params = get_params()
    print("Params: %s" % params)

    # 1-2. Weak-label with the LLM, persist for auditability.
    labeled = weak_label_dataframe(spark, params)
    labeled.write.mode("overwrite").format("delta") \
        .option("overwriteSchema", "true").saveAsTable(params["weak_labels_table"])
    pdf = labeled.select("words", "label_str").toPandas()
    print("Weak-labeled rows: %d" % len(pdf))
    if len(pdf) < 20 or pdf["label_str"].nunique() < 2:
        print("WARNING: too few rows / classes to train a meaningful model "
              "(expected on synthetic sample data). Logging a stub run and exiting.")
        _log_stub(params, len(pdf), pdf["label_str"].nunique())
        return

    pdf["label"] = pdf["label_str"].map(LABEL2ID)

    # 3-5. Fine-tune DistilBERT and log to MLflow.
    import evaluate
    import torch
    from datasets import Dataset
    from sklearn.model_selection import train_test_split
    from transformers import (AutoModelForSequenceClassification, AutoTokenizer,
                              DataCollatorWithPadding, Trainer, TrainingArguments)

    train_df, test_df = train_test_split(
        pdf, test_size=float(params["test_size"]), random_state=42,
        stratify=pdf["label"] if pdf["label"].nunique() > 1 else None)

    tokenizer = AutoTokenizer.from_pretrained(params["base_model"])

    def tok(batch):
        return tokenizer(batch["words"], truncation=True, max_length=128)

    ds_train = Dataset.from_pandas(train_df[["words", "label"]]).map(tok, batched=True)
    ds_test = Dataset.from_pandas(test_df[["words", "label"]]).map(tok, batched=True)

    model = AutoModelForSequenceClassification.from_pretrained(
        params["base_model"], num_labels=len(LABELS),
        id2label=ID2LABEL, label2id=LABEL2ID)

    acc = evaluate.load("accuracy")
    f1 = evaluate.load("f1")

    def metrics(eval_pred):
        logits, labels = eval_pred
        preds = np.argmax(logits, axis=-1)
        return {
            "accuracy": acc.compute(predictions=preds, references=labels)["accuracy"],
            "macro_f1": f1.compute(predictions=preds, references=labels,
                                   average="macro")["f1"],
        }

    # Single-node training only. Databricks compute (serverless especially) sets
    # PyTorch distributed env vars (RANK, WORLD_SIZE, MASTER_ADDR/PORT, ...).
    # HuggingFace TrainingArguments auto-detects these and tries to join a
    # distributed process group at localhost:43111 — but there's no coordinator
    # in a single-node job, so it hangs 30 min -> DistNetworkError. Clearing them
    # keeps the Trainer in plain single-process mode. Harmless on any compute.
    for _var in ("LOCAL_RANK", "RANK", "WORLD_SIZE", "MASTER_ADDR", "MASTER_PORT"):
        os.environ.pop(_var, None)

    out_dir = "/tmp/gm_voc_sentiment"
    args = TrainingArguments(
        output_dir=out_dir,
        num_train_epochs=float(params["num_train_epochs"]),
        per_device_train_batch_size=int(params["batch_size"]),
        per_device_eval_batch_size=int(params["batch_size"]),
        eval_strategy="epoch",
        save_strategy="no",
        logging_steps=10,
        report_to=[],
    )
    trainer = Trainer(
        model=model, args=args,
        train_dataset=ds_train, eval_dataset=ds_test,
        tokenizer=tokenizer,
        data_collator=DataCollatorWithPadding(tokenizer),
        compute_metrics=metrics)

    mlflow.set_experiment(params["experiment_path"])
    mlflow.set_registry_uri("databricks-uc")
    with mlflow.start_run(run_name="distilbert_5class_sentiment") as run:
        mlflow.log_params({
            "base_model": params["base_model"],
            "num_train_epochs": params["num_train_epochs"],
            "batch_size": params["batch_size"],
            "train_rows": len(train_df), "test_rows": len(test_df),
            "label_endpoint": params["label_endpoint"],
            "label_scheme": ",".join(LABELS),
            "weak_labels": "llm_zero_shot",
        })
        trainer.train()
        eval_metrics = trainer.evaluate()
        mlflow.log_metrics({k: float(v) for k, v in eval_metrics.items()
                            if isinstance(v, (int, float))})

        # Log as a transformers pipeline flavor and register in Unity Catalog.
        import transformers
        pipe = transformers.pipeline(
            "text-classification", model=model, tokenizer=tokenizer,
            device=0 if torch.cuda.is_available() else -1)
        mlflow.transformers.log_model(
            transformers_model=pipe,
            artifact_path="model",
            registered_model_name=params["registered_model"],
            task="text-classification")
        print("Logged + registered %s (run %s)" % (
            params["registered_model"], run.info.run_id))
        print("Eval: %s" % eval_metrics)


def _log_stub(params, n_rows, n_classes):
    """When data is too thin to train (e.g. synthetic sample), still record a
    run documenting why, so the pipeline is demonstrably wired end-to-end."""
    import mlflow
    mlflow.set_experiment(params["experiment_path"])
    with mlflow.start_run(run_name="sentiment_train_skipped_insufficient_data"):
        mlflow.log_params({"base_model": params["base_model"],
                           "weak_labeled_rows": n_rows,
                           "distinct_classes": n_classes})
        mlflow.set_tag("status", "skipped_insufficient_data")
        mlflow.log_metric("weak_labeled_rows", n_rows)
    print("Logged stub run; skipped training due to insufficient data.")


if __name__ == "__main__":
    run()
