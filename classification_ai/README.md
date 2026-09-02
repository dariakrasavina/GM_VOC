# Classification — AI (Databricks AI functions)

The AI-powered classification track: instead of hand-maintained keyword rules, a
**hosted LLM** reads each sentence and decides which of the 4 POC categories
apply — by *meaning*, so it catches paraphrases the rules miss. This folder holds
**three implementations** of that idea (so they can be compared) plus the job
that scores them against the rule engine.

All three call **hosted Databricks foundation models** (not trained or owned) —
non-deterministic and pay-per-call, so runs are bounded by `sample_limit`.

---

## The three approaches

All classify against the **same** `shared/category_model.json`, **dedup** first
(classify each *distinct* sentence once, then fan the tag back to every row that
shares it), **roll up** leaf matches to parent categories, and write the **same
output shape** (one 0/1 column per category). They differ only in *which function*
and *how they execute*:

| Module | Function | Labels | Execution | Output table |
|---|---|---|---|---|
| `ai_query_job.py` | `ai_query` + custom prompt | **multi**-label | PySpark, serverless compute | `voc_classification_ai_query_tags` |
| `ai_query_sql.py` | `ai_query` + custom prompt | **multi**-label | **DBSQL batch** on a SQL warehouse | `voc_classification_ai_query_sql_tags` |
| `ai_classify.py` | built-in `ai_classify()` | **single**-label (+ "None of the above") | DBSQL batch on a SQL warehouse | `voc_classification_ai_classify_tags` |

**Which to use:** `ai_query_sql.py` is the scalable path — the PySpark version's
`ai_query` concurrency is bounded by partition count and crawls on large days,
whereas the DBSQL batch path lets `ai_query` drive concurrency to the endpoint
directly. `ai_classify.py` is a genuinely different *method* (single best label,
no definitions) worth comparing, not a scaling fix.

---

## How the pieces fit together

```
shared/category_model.json         category names + definitions (shared with rules)
        │
        ├─► ai_query_job.py    (PySpark)     ──► voc_classification_ai_query_tags
        │     run_ai_query_notebook.py
        │
        ├─► ai_query_sql.py    (DBSQL batch) ──► voc_classification_ai_query_sql_tags
        │     run_ai_query_sql_notebook.py
        │
        └─► ai_classify.py     (built-in)    ──► voc_classification_ai_classify_tags
              run_ai_classify_notebook.py

compare_approaches_job.py  ── rule tags + ai_query tags ──► voc_classification_comparison
        run_compare_notebook.py
```

---

## File-by-file

### `ai_query_job.py` — `ai_query`, multi-label (PySpark)
Classifies each in-scope sentence into the 4 categories, **multi-label + roll-up**
(same output shape as the rule engine, so they're comparable).

- `build_prompt()` — instruction: "return ALL categories that apply as a JSON
  array", with each category's business definition to steer the LLM.
- `run()` — scope-filter in SQL → `SELECT DISTINCT words` → `ai_query(endpoint,
  prompt + sentence)` on the distinct sentences → parse the JSON array →
  `expand_ids()` maps names→ids and rolls up to parents → one 0/1 column per
  category → **join back to all rows** → write.
- **Serverless-safe:** materializes intermediate results to Delta tables (not
  `.persist()`, which serverless forbids) so `ai_query` runs exactly once.

### `ai_query_sql.py` — `ai_query`, multi-label (DBSQL batch)
Same classification logic, expressed as **set-based SQL** (`CREATE TABLE AS SELECT
ai_query(...)`) submitted to a SQL warehouse via the Statement Execution API.

- The roll-up is compiled into SQL (`arrays_overlap` over precomputed per-category
  trigger names) — no UDF.
- **Frequency-prioritized:** distinct sentences are ordered **most-frequent-first**,
  so `sample_limit = N` classifies the N sentences that cover the largest share of
  the day's rows (contact-center text is skewed — a few sentences cover many rows).
- Requires `warehouse_id`.

### `ai_classify.py` — built-in `ai_classify()`, single-label (DBSQL batch)
Uses the native `ai_classify(text, ARRAY(labels))` — picks **one** best label.

- An explicit **"None of the above"** label lets a sentence opt out (otherwise
  every row would be force-tagged); rows labeled "None" get no category.
- The chosen leaf label is rolled up to its parents (`ai_label IN (...)`), so the
  output shape matches the other tracks.
- Requires `warehouse_id`. Takes only bare label names (no definitions), so
  accuracy may differ — that's what the comparison is for.

### `compare_approaches_job.py` — rule engine vs. AI agreement
The "regression vs. control" deliverable. Scoped to a single day (`compare_date`).
`run()` joins `voc_classification_rule_tags` and `voc_classification_ai_query_tags`
on `id_verbatim` and, per category, computes rule-positives, AI-positives,
agreement, rule-only, AI-only, and **precision / recall / F1 of the AI using the
rule engine as proxy ground truth**. Writes `voc_classification_comparison`.
(Compares the PySpark `ai_query` table by default; point it at another AI table to
compare that one.)

### `ab_test_prompt.py` — prompt A/B harness (diagnostic)
Runs two prompts (current vs. a candidate) **plus a second current-prompt pass as
a model-noise floor** over a sample, and reports agreement. Because `ai_query` is
non-deterministic, the noise floor separates real prompt-driven changes from
random variation. Use before changing the production prompt (or extend it to
compare models).

### Notebook entrypoints
Thin wrappers that add the folder to `sys.path`, import the module, and call
`run()`: `run_ai_query_notebook.py`, `run_ai_query_sql_notebook.py`,
`run_ai_classify_notebook.py`, and `run_compare_notebook.py` (run compare **after**
the rule job and an AI job have produced their tag tables).

---

## Requirements & caveats
- **Serverless + DBR 18.2+**, Model-Serving region. The DBSQL-batch jobs also need
  a SQL warehouse (`warehouse_id`) that supports AI functions.
- **Pay-per-call** — each *distinct* sentence is one LLM call (dedup means you pay
  per unique sentence, not per row). `sample_limit` bounds it.
- **Throughput ceiling.** A shared **pay-per-token** endpoint rate-limits large
  batches: a full single day (~1.3M distinct sentences) runs ~11–14 h and is
  best served by a **provisioned-throughput** endpoint. Dedup + frequency-first
  sampling mitigate; a faster model does **not** raise a QPS cap.
- **Non-deterministic** — the LLM varies run-to-run and tends to *over-tag*
  (higher recall, lower precision) vs. the strict rules. The compare job
  quantifies where they diverge.
- Not to be confused with **topic modeling** (`../topic_modeling/`), which
  *discovers new themes* rather than assigning the predefined categories.
