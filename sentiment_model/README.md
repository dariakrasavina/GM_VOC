# Sentiment Model (trained transformer + lexicon baseline)

The **trained ML** track — rates each sentence's sentiment on a 5-point scale
(Very Negative … Very Positive). This is a *different job* from the classification
tracks (which answer "what category?"); sentiment answers "how does the customer
feel?" and is meant to be used **alongside** the winning classification track.

**The only track that trains and registers a model you own:** a fine-tuned
DistilBERT transformer, logged to MLflow / Unity Catalog. A simple word-list
method (VADER) is scored beside it as a transparent baseline.

---

## How it works

```
TRAIN (train_sentiment_model.py)
  sentences ─► ai_query weak-labels (LLM) ─► fine-tune DistilBERT ─► MLflow register
                                                                          │
SCORE (score_sentiment.py)                                                ▼
  sentences ─► load registered model ─► transformer_label + VADER label ─► voc_sentiment_scored
```

---

## File-by-file

### `train_sentiment_model.py` — weak-label + fine-tune + register (the core)
| Function | What it does |
|---|---|
| `get_params()` | table names, endpoints, `base_model`, epochs, `sample_limit`, MLflow targets |
| `weak_label_dataframe()` | **the label bootstrap** — GM has no labels, so `ai_query` (LLM) assigns a 5-class sentiment to each scoped sentence; parsed to a clean label |
| `run()` | fine-tune + log/register (below) |
| `tok()` | tokenizes text for the model (HuggingFace tokenizer) |
| `metrics()` | accuracy + macro-F1 on a held-out split |
| `_log_stub()` | if data is too thin/degenerate to train, logs a "skipped" MLflow run instead of training garbage |

`run()` step by step:
1. Scope + sample sentences; **weak-label** them via the LLM; persist labels to
   `voc_sentiment_weak_labels` (audit trail).
2. If too few rows / classes → `_log_stub()` and stop (expected on flat data).
3. Otherwise **fine-tune DistilBERT** (HuggingFace `Trainer`) on the weak labels.
   *(A guard clears PyTorch distributed env vars first so the Trainer stays
   single-process — otherwise it hangs on Databricks.)*
4. Evaluate (accuracy / macro-F1), **log params + metrics + model to MLflow**,
   and **register** it in Unity Catalog (`voc_sentiment_transformer`).

### `score_sentiment.py` — batch scoring (transformer + baseline)
| Function | What it does |
|---|---|
| `get_params()` | table names, registered-model name, `model_alias` |
| `_load_transformer()` | load the registered model from Unity Catalog; returns None if not registered yet |
| `run()` | score sentences with the transformer (if available) **and** VADER, write both |

Output columns in `voc_sentiment_scored`:
- `transformer_label` / `transformer_score` — the trained model's label + its
  **confidence** (0–1). **Null if no model is registered yet** (falls back to
  VADER-only).
- `vader_label` / `vader_compound` — the baseline's label + its **polarity**
  score (−1..+1, the intensity the label is derived from — *not* a confidence).

### `vader_baseline.py` — lexicon sentiment baseline
| Item | What it does |
|---|---|
| `VaderScorer` | wraps the `vaderSentiment` library; `score(text)` → (label, compound) |
| `_bucket()` | maps VADER's −1..+1 compound score into the 5 classes |
| `_fallback_score()` | tiny built-in lexicon used if `vaderSentiment` isn't installed |
| `score_dataframe()` | Spark helper to score a table with a UDF |
Runs locally with no GPU — the one piece verifiable off-cluster.

### `run_train_sentiment_notebook.py` / `run_score_sentiment_notebook.py`
Databricks entrypoints: `%pip install` the extras (evaluate, vaderSentiment;
torch/transformers are preinstalled on the GPU ML runtime), add folder to
`sys.path`, call the job's `run()`.

---

## Inputs / outputs
- **Reads:** the sentence table.
- **Writes:** `voc_sentiment_weak_labels` (audit), the registered model
  `voc_sentiment_transformer` (MLflow/UC), and `voc_sentiment_scored`.

## Requirements & caveats
- **Runs on a single-node GPU cluster** (Databricks Runtime ML for GPU) — set in
  `databricks.yml`. Serverless has no GPU and the Trainer's distributed init
  times out there; the cluster also needs `SINGLE_USER` mode for Unity Catalog.
- **Labels are bootstrapped by an LLM**, not human ground truth — so the model's
  ceiling is the LLM's quality until a human-adjudicated gold set exists.
- On flat/synthetic data, weak labels collapse to one class → training correctly
  **skips** (stub run), and `voc_sentiment_scored` shows **VADER only**
  (transformer columns null). Real transcripts are needed for a real model.
