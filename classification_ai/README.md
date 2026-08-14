# Classification — AI (Databricks AI functions)

The lightweight AI-powered classification track. Instead of hand-maintained
keyword rules, an **LLM** reads each sentence and decides which categories apply
— by *meaning*, so it catches paraphrases the rules miss. It also produces
sentiment, and this folder holds the job that **compares** it against the rule
engine.

**Uses a hosted foundation model** (not trained or owned — you call Databricks AI
functions). Non-deterministic and pay-per-row, so runs are capped by
`sample_limit`.

---

## How the pieces fit together

```
shared/category_model.json          category names + definitions (shared with rules)
        │
        ▼
ai_classify_job.py   LLM assigns categories (multi-label) + sentiment
        │                     │
run_ai_classify_notebook.py   └──► voc_classification_ai_tags
                                        │
compare_approaches_job.py  ── reads rule tags + AI tags ──► voc_classification_comparison
run_compare_notebook.py
```

---

## File-by-file

### `ai_classify_job.py` — LLM classification + sentiment (the core)
Classifies each in-scope sentence into the 4 POC categories, **multi-label +
roll-up** (same output shape as the rule engine so the two are comparable).

Key functions and their logic:

| Function | What it does | Logic used |
|---|---|---|
| `get_params()` | resolve table names, endpoints, `sample_limit` | widgets / argv / defaults |
| `load_categories()` | read `category_model.json` | builds label→definition, id map, **ancestors** for roll-up |
| `build_prompt()` | craft the LLM instruction | "return ALL categories that apply as a JSON array", with each category's business definition |
| `run()` | the pipeline | see below |
| `expand_ids()` | map returned names → ids + roll up | a sentence matching a leaf also gets its parent categories |

`run()` step by step:
1. Scope-filter in SQL (English + audio + customer-side + date).
2. **`ai_query(endpoint, prompt + sentence)`** → the LLM returns a JSON array of
   all applicable category names (true multi-label — *not* the single-label
   `ai_classify` builtin, which is why `ai_query` is used with a custom prompt).
3. Parse the array, map names→ids, **roll up to ancestors** (via `expand_ids`).
4. **`ai_analyze_sentiment(words)`** → positive / negative / neutral / mixed.
5. Explode to one 0/1 column per category (leaves + parents) + `ai_categories`
   (raw LLM output) + `sentiment`; write `voc_classification_ai_tags`.

Reads categories from the **same `category_model.json`** the rule engine uses, so
both classifiers judge against identical categories.

### `compare_approaches_job.py` — rule engine vs. AI agreement
The cross-track comparison (the "regression vs. control" deliverable). Logic:
- `all_categories()` — list every category from the model.
- `run()` — joins `voc_classification_rule_tags` and `voc_classification_ai_tags`
  on `id_verbatim`; for each category computes: rule-positives, AI-positives,
  agreement, rule-only, AI-only, and **precision / recall / F1 of the AI using
  the rule engine as proxy ground truth**. Writes `voc_classification_comparison`.
- Compares **all** categories present in both tables (leaves + rolled-up parents),
  column-by-column — apples-to-apples because both sides share the output shape.

### `run_ai_classify_notebook.py` — Databricks entrypoint
Thin notebook: adds folder to `sys.path`, imports `ai_classify_job`, calls
`run()`.

### `run_compare_notebook.py` — Databricks entrypoint
Thin notebook: imports `compare_approaches_job`, calls `run()`. Run this **after**
both the rule job and the AI classify job have produced their tag tables.

---

## Inputs / outputs

- **`ai_classify_job`** reads the sentence table + `category_model.json`; writes
  `voc_classification_ai_tags` (per-category 0/1 + sentiment, multi-label + roll-up).
- **`compare_approaches_job`** reads `voc_classification_rule_tags` +
  `voc_classification_ai_tags`; writes `voc_classification_comparison`.

## Requirements & caveats
- **Serverless + DBR 18.2+**, Model-Serving region (AI functions).
- **Pay-per-row** — each sentence is an LLM call. `sample_limit` caps this
  (default 200); set `0` for the full corpus once cost is understood.
- **Non-deterministic** — the LLM can vary run-to-run and tends to *over-tag*
  (higher recall, lower precision) vs. the strict rules. The compare job
  quantifies exactly where they diverge.
- Not to be confused with **topic modeling** (`../topic_modeling/`), which
  *discovers new themes* rather than assigning the predefined categories.
