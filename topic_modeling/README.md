# Topic Modeling (unsupervised theme discovery)

Finds themes **nobody predefined** by grouping semantically similar sentences.
Unlike the classification tracks (which assign the 4 known categories), this
*discovers* what topics exist in the data — the exploratory counterpart, and the
"global other" review a rule engine fundamentally cannot do.

**Uses two models:** a hosted **embedding model** (to turn text into vectors) and
a **KMeans model trained at run time** on your data (the one genuinely-trained
model in this track). Names each cluster with an LLM.

---

## How it works

```
sentences ──► ai_query (embeddings) ──► vectors ──► KMeans clusters ──► ai_gen names
                                                          │
                                                          ▼
                                     voc_topicmodeling_themes  (theme + name + summary)
                                     voc_topicmodeling_assignments  (sentence → theme_id)
```

Runs at **sentence level** (one embedding per sentence; each sentence gets one
theme).

---

## File-by-file

### `topic_discovery_job.py` — the pipeline (the core)
| Function | What it does |
|---|---|
| `get_params()` | resolve table names, endpoints, `num_clusters`, `sample_limit` (widgets / argv / defaults) |
| `run()` | the full discovery pipeline (below) |

`run()` step by step:
1. **Scope** — SQL filter to English + audio + customer-side + date range
   (optionally capped by `sample_limit`, since embeddings are pay-per-token).
2. **Embed** — `ai_query('databricks-gte-large-en', words)` returns a dense
   vector per sentence (a hosted embedding model — not trained here).
3. **Cluster** — collects the vectors to the driver and runs **scikit-learn
   KMeans** (`num_clusters`, default 12). *Why sklearn on the driver, not Spark
   MLlib: serverless forbids the RDD persistence MLlib KMeans needs. This is fine
   for the bounded sample; for a huge corpus on classic compute, swap to
   `pyspark.ml` KMeans.*
4. **Name** — for each cluster, take a few example sentences and ask **`ai_gen`**
   for a `NAME:` / `SUMMARY:` (a rigid two-line text format, parsed with line
   regex — chosen because JSON from the LLM was fragile: markdown fences and
   embedded newlines produced null names).
5. **Write** — `voc_topicmodeling_themes` (theme_id, size, name, summary,
   examples) and `voc_topicmodeling_assignments` (sentence → theme_id).

### `run_topic_discovery_notebook.py` — Databricks entrypoint
Thin notebook: adds folder to `sys.path`, imports the job, calls `run()`.

---

## Inputs / outputs
- **Reads:** the sentence table (uses only `words` + ids; no category model — it
  invents its own themes).
- **Writes:** `voc_topicmodeling_themes`, `voc_topicmodeling_assignments`.

## Notes & caveats
- **Serverless + DBR 18.2+**, Model-Serving region; embeddings + `ai_gen` are
  **pay-per-token** → `sample_limit` bounds cost.
- `size` = number of sentences in a theme (its volume). Bigger = more common.
- **Sentence-level limitation:** very short, context-free lines ("Yes.", "One
  moment please") cluster by surface form, not meaning. Conversation-level
  discovery (concatenate a call's sentences before embedding) would give more
  interpretable "what are calls about" themes — not built, but a small change.
- This is **discovery**, so it is *not* compared to the rule engine (there's no
  shared label space — themes are unnamed clusters, not the 4 categories).
