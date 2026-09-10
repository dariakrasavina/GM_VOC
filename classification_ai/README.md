# Classification — AI (Databricks AI functions)

The AI-powered classification track: instead of hand-maintained keyword rules, a
**hosted LLM** reads each sentence and decides which of the 4 POC categories
apply — by *meaning*, so it catches paraphrases the rules miss. This folder holds
**two implementations** of that idea (so they can be compared) plus the job that
scores them against the rule engine.

Both call **hosted Databricks foundation models** (not trained or owned) —
non-deterministic and pay-per-call, so runs are bounded by `sample_limit`. Shared
category-loading + prompt code lives in `ai_common.py`.

---

## The two approaches

Both classify against the **same** `shared/category_model.json`, **dedup** first
(classify each *distinct* sentence once, then fan the tag back to every row that
shares it), **roll up** leaf matches to parent categories, and write the **same
output shape** (one 0/1 column per category). They differ in *which function* and
*how they steer the model*:

| Module | Function | Labels | Model | Output table |
|---|---|---|---|---|
| `ai_query_sql.py` | `ai_query` + custom prompt | **multi**-label | your choice (Sonnet 4.6) | `voc_classification_ai_query_sql_tags` |
| `ai_classify.py` | built-in `ai_classify()` **v2.1** | **multi**-label + descriptions + confidence | fixed managed | `voc_classification_ai_classify_tags` |

Both run as **DBSQL batch** on a SQL warehouse (`CREATE TABLE AS SELECT …`), so the
engine drives concurrency to the endpoint directly.

**Which to use:** `ai_query_sql.py` is the higher-quality, model-selectable path
(custom prompt + Sonnet 4.6). `ai_classify.py` (v2.1) is a purpose-built classifier
with label descriptions, multi-label output, and confidence scores, but on a
**fixed managed model** (no model choice) — worth comparing on cost/quality.

> A per-partition PySpark version (`ai_query_job.py`) was removed — it was bounded
> by partition count and crawled on large days; the DBSQL-batch path supersedes it.

---

## How the pieces fit together

```
shared/category_model.json ──► ai_common.py  (load categories + build prompt)
        │
        ├─► ai_query_sql.py    (ai_query, DBSQL batch) ──► voc_classification_ai_query_sql_tags
        │     run_ai_query_sql_notebook.py
        │
        └─► ai_classify.py     (ai_classify v2.1)      ──► voc_classification_ai_classify_tags
              run_ai_classify_notebook.py

compare_approaches_job.py  ── rule tags + an AI tags table ──► voc_classification_comparison
        run_compare_notebook.py
```

---

## File-by-file

### `ai_common.py` — shared helpers (no pyspark)
`find_category_model()` / `load_categories()` locate and parse the shared
`category_model.json`; `build_prompt()` builds the multi-label `ai_query` prompt.
Imported by both classifiers and the A/B harness so they use identical categories
and prompt text.

### `ai_query_sql.py` — `ai_query`, multi-label (DBSQL batch)
Same classification logic, expressed as **set-based SQL** (`CREATE TABLE AS SELECT
ai_query(...)`) submitted to a SQL warehouse via the Statement Execution API.

- The roll-up is compiled into SQL (`arrays_overlap` over precomputed per-category
  trigger names) — no UDF.
- **Sampling (`sample_mode`):** when `sample_limit = N`, distinct sentences are
  sampled **randomly by default** (representative — fair for evaluation). Set
  `sample_mode=frequency` for a capped *production* run to score the most-common
  sentences first (max row coverage). With `sample_limit=0` all distinct sentences
  are classified, so the mode is moot. (The FAQ doesn't prescribe frequency-first;
  it over-samples short filler, which skews small evals.)
- Requires `warehouse_id`.

### `ai_classify.py` — built-in `ai_classify()` **v2.1** (DBSQL batch)
Uses the purpose-built `ai_classify(text, labels, MAP('version','2.1', ...))`.

- **Labels with descriptions:** labels are a JSON object `{category: definition}`,
  so the managed model sees each category's business definition (no cryptic
  ≤50-char labels). Reuses the same definitions from `category_model.json`.
- **Multi-label** (`'multilabel'='true'`): returns ALL applicable categories — a
  sentence that matches nothing returns an empty list, so no synthetic "None of
  the above" label is needed.
- **Confidence scores** (`enableConfidenceScores`) are captured (0–1) for triage;
  **rationales** (`enableRationales`) are OFF by default (extra output tokens —
  enable for QA). Errors surface in the result's `error_message` (no `failOnError`
  needed).
- **Global `instructions`** steer the managed model the way the ai_query prompt
  steers ai_query ("most sentences match nothing", etc.).
- The returned label names are rolled up to parents with `arrays_overlap` — the
  SAME roll-up as `ai_query_sql` — so the output shape matches the other tracks.
- Requires `warehouse_id`. Still a **fixed managed model** (you cannot choose
  Claude/GPT), so accuracy may differ from the Sonnet `ai_query` path — that's
  what the comparison job is for.

### `compare_approaches_job.py` — rule engine vs. AI agreement
The "regression vs. control" deliverable. Scoped to a single day (`compare_date`).
`run()` joins `voc_classification_rule_tags` and an AI tags table on `id_verbatim`
and, per category, computes rule-positives, AI-positives, agreement, rule-only,
AI-only, and **precision / recall / F1 of the AI using the rule engine as proxy
ground truth**. Writes `voc_classification_comparison`. (Defaults to
`voc_classification_ai_query_sql_tags`; point `ai_tags_table` at
`voc_classification_ai_classify_tags` to compare that one.)

### `ab_test_prompt.py` — prompt A/B harness (diagnostic)
Runs two prompts (current vs. a candidate) **plus a second current-prompt pass as
a model-noise floor** over a sample, and reports agreement. Because `ai_query` is
non-deterministic, the noise floor separates real prompt-driven changes from
random variation. Use before changing the production prompt (or extend it to
compare models).

### Notebook entrypoints
Thin wrappers that add the folder to `sys.path`, import the module, and call
`run()`: `run_ai_query_sql_notebook.py`, `run_ai_classify_notebook.py`, and
`run_compare_notebook.py` (run compare **after** the rule job and an AI job have
produced their tag tables).

---

## Requirements & caveats
- **Serverless + DBR 18.2+**, Model-Serving region. The DBSQL-batch jobs also need
  a SQL warehouse (`warehouse_id`) that supports AI functions.
- **Scoped to a single day** (`classify_date`, default `2026-06-11`), independent of
  the rule engine's date window, so an AI run is a bounded, comparable slice. The
  DBSQL jobs also take an optional `hour_start`/`hour_end` window. Default endpoint:
  **`databricks-claude-sonnet-4-6`** (best quality of the models tested).
- **Pay-per-call** — each *distinct* sentence is one LLM call (dedup means you pay
  per unique sentence, not per row). `sample_limit` bounds it.
- **Throughput ceiling.** A shared **pay-per-token** endpoint rate-limits large
  batches. Measured on a full single day (~1.25M distinct sentences, ~2.2M rows):
  the built-in `ai_classify` (fixed managed model) runs at ~4,800 distinct
  sentences/min (~4–5 h/day); the `ai_query` Sonnet path is rate-limited slower
  (~2,900/min, ~7 h/day). The full dataset (a year) needs a
  **provisioned-throughput** endpoint (self-serve for open-weight models; a
  committed-throughput request via Databricks for Claude). Dedup mitigates, but a
  faster model does **not** raise a QPS cap.

> **Visual walkthrough:** the full `ai_query_sql` / `ai_classify` logic (the three
> batch-SQL statements, dedup vs. context, roll-up compiled to `arrays_overlap`,
> and the scaling discussion) is diagrammed in the deep-dive deck —
> `GM_VOC_Technical_Deep_Dive.pptx`, generated by `docs/make_deepdive_pptx.py`.
- **Non-deterministic** — the LLM varies run-to-run and tends to *over-tag*
  (higher recall, lower precision) vs. the strict rules. The compare job
  quantifies where they diverge.
- Not to be confused with **topic modeling** (`../topic_modeling/`), which
  *discovers new themes* rather than assigning the predefined categories.
