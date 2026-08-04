# GM VOC POC — Track 3: Trained Sentiment Model

The **trained ML** track. It rates each sentence's sentiment on a 5-point scale
(Very Negative … Very Positive) using a fine-tuned DistilBERT transformer, with a
simple word-list method (VADER) scored alongside as a baseline.

This is a **different job** from Tracks 1 and 2 (which tag topics). Track 3 adds
"how does the customer feel?" and is meant to be used **alongside** whichever
topic track GM picks — not compared against them.

## Why a single transformer (not an ensemble)

We use one trained model, not an ensemble of models, because:

- **No labeled data** is the real constraint — a bigger ensemble can't help until
  there are labels to train on.
- Ensembles are **harder to explain** and less repeatable — the two things GM
  values most.
- The accuracy gain over a single good model is small (~1–2 points).

So Track 3 is one transformer, plus a transparent word-list baseline.

## The label problem — bootstrapped labels

Training needs labeled examples, and GM has none yet. So we **bootstrap labels
with an LLM** (`ai_query`), train on those, and treat the result as a *starting*
model to refine once people review real data. It is not ground truth.

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
| `vader_baseline.py` | Simple word-list (VADER) 5-class baseline for comparison; runs locally, no GPU. |
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
