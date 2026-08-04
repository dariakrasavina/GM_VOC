# GM VOC POC — Track 3: Trained Transformer Sentiment Model

The Qualtrics-faithful **trained ML** track. Qualtrics' own engineering writeup
([sentiment-analysis-with-text-iq](https://www.qualtrics.com/news/sentiment-analysis-with-text-iq/))
describes their sentiment system as evolving from a **lexicon + shallow neural
net** hybrid to a **single transformer-based deep-learning model** emitting
**5 classes** (Very Positive … Very Negative). This track reproduces exactly that:
a trained DistilBERT transformer, with a VADER lexicon baseline standing in for
the older lexicon era.

## Why a single transformer (and explicitly not an ensemble)

Research into Qualtrics/Clarabridge found **no documented use of ensemble models**
(random forest / boosting / voting / stacking); their disclosed sentiment engine
is a *single* transformer. Ensembles were also assessed as a poor fit for this
POC specifically:

- **No labeled data** is the binding constraint — ensembles are a supervised
  accuracy optimization that presupposes labels.
- Ensembles **hurt explainability and determinism**, the two things GM values.
- The accuracy gain over a single fine-tuned transformer is marginal (~1–2 pts).

So Track 3 is one transformer, plus a transparent lexicon baseline.

## The label problem — bootstrapped weak supervision

Supervised training needs labels; GM has none yet. Following the literature, we
**bootstrap weak labels with a zero-shot LLM** (`ai_query`), fine-tune on those,
and treat the result as a *starting* model to refine once humans adjudicate a
gold set. This is weak supervision, documented as such — not ground truth.

## Pipeline

```
train_sentiment_model.py
  1. Scope + sample in-scope customer verbatims (EN / audio / customer-side).
  2. Weak-label each 1..5 sentiment class via ai_query (zero-shot LLM).
  3. Fine-tune DistilBERT (HuggingFace Trainer) on the weak labels.
  4. Evaluate accuracy / macro-F1 on a held-out split.
  5. Log params/metrics/model to MLflow; register in Unity Catalog.

score_sentiment.py
  - Load the registered transformer from UC; score verbatims.
  - Also score with the VADER lexicon baseline (vader_baseline.py).
  - Write both per sentence for comparison.
```

## Files

| File | Role |
|------|------|
| `train_sentiment_model.py` | Weak-label + fine-tune DistilBERT + MLflow log/register. |
| `vader_baseline.py` | Lexicon (VADER) 5-class baseline — the pre-transformer era; runs locally, no GPU. |
| `score_sentiment.py` | Batch-score with the registered transformer + VADER; write comparison table. |
| `run_train_sentiment_notebook.py` / `run_score_sentiment_notebook.py` | Databricks entrypoints (install libs, call the job). |

## Requirements

- **Compute:** the bundle runs this on **serverless**, which has **no GPU and no
  pre-installed PyTorch**. The notebooks therefore `%pip install torch accelerate
  transformers datasets evaluate vaderSentiment`. Fine-tuning DistilBERT on
  serverless CPU is fine for the small POC sample (a few hundred rows) but would
  be slow at scale — for a real training run, use a **GPU cluster on Databricks
  Runtime for ML** (where torch is preinstalled and training is much faster).
- Libraries (installed by the notebooks): `torch`, `accelerate`, `transformers`,
  `datasets`, `evaluate`, `vaderSentiment`; `mlflow` is always present.
- `ai_query` weak-labeling needs serverless / Model-Serving access + a chat endpoint.
- Unity Catalog for model registration (`mlflow.set_registry_uri("databricks-uc")`).

## Output tables / artifacts (`daria_krasavina.gm_voc`)

| Artifact | Produced by | Contents |
|----------|-------------|----------|
| `voc_sentiment_weak_labels` | train | verbatim + LLM weak label (audit trail) |
| MLflow experiment `/Shared/gm_voc_sentiment` | train | params, accuracy, macro-F1, the model |
| registered model `voc_sentiment_transformer` | train | the fine-tuned DistilBERT (UC Model Registry) |
| `voc_sentiment_scored` | score | transformer_label/score + vader_label/compound per sentence |

## Running

```bash
databricks bundle deploy -t sandbox -p <profile>
databricks bundle run  voc_sentiment_model_job -t sandbox -p <profile>
```

The VADER baseline is also runnable locally for a quick sanity check:

```bash
python3 sentiment_model/vader_baseline.py
```

## Honest caveats

- **On the synthetic sample data the transformer will be degenerate** — there is
  no real sentiment signal, and weak-labeling will likely collapse to one class.
  `train_sentiment_model.py` detects too-few-rows/classes and logs a *stub* MLflow
  run explaining the skip rather than training garbage. The deliverable now is the
  **method + MLflow train→log→register lifecycle**; accuracy needs real verbatims.
- **This code has not been executed** — transformers need a GPU + heavy libraries
  not available in the local dev environment, so it was validated for
  structure/compile only. Expect to shake out runtime details (library versions,
  the `ai_query` return shape, MLflow transformers flavor specifics) on the first
  real Databricks run.
- **Weak labels ≈ imitating an LLM.** Until humans adjudicate a gold set, the
  model's ceiling is the zero-shot LLM's quality. Plan a labeling/adjudication
  step before trusting metrics.
