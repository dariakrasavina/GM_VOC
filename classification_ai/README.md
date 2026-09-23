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

## What makes it "AI" — same taxonomy, different fields

Both AI tracks classify against the **same** `shared/category_model.json` that the rule
engine uses — but the AI reads a **different part of it**. `ai_classify` (via
`build_labels_json` → `load_categories`) pulls each target's **`ai_description`** — its
plain-English classifier definition (falling back to the customer-authored `description`
if a node has none). **It never sees the keyword lanes.** The rule engine does the
opposite: it runs the **`lanes`** (keywords / and / and2 / not) and treats the
descriptions as comments.

They read different parts of `category_model.json`:

| Field in each node | Rule engine uses it? | `ai_classify` uses it? |
|---|---|---|
| `lanes` (keywords / and / and2 / not) | ✅ **this is the rule** | ❌ never sees it |
| `ai_description` (classifier definition) | ❌ ignored | ✅ **this is what it sends the model** |
| `description` (customer's note) | ❌ just documentation | ✅ only as a fallback if `ai_description` is absent |
| `comparison_target` | flags `is_target` (which categories the comparison scores) | ✅ **only `true` nodes are sent as labels** |
| `name` | for output | ✅ the label |
| `path` (hierarchy) | ✅ roll-up | ✅ roll-up |

So the **"AI part" is semantic judgment instead of literal matching.** Rather than testing
whether a sentence contains specific tokens/phrases (with AND/NOT/wildcard/proximity
conditions), the hosted model reads the label's *definition* and decides, **by meaning**,
whether the sentence fits. That's why it catches paraphrases the keyword rules miss — and
also why it's non-deterministic and can over-tag.

Concrete contrast, the "Confusing" category:

- **Rule engine** sees the lanes: `confused, confusing, "makes no sense", bewilder*, …
  NOT ("no confusion")` → fires only on those literal terms.
- **`ai_classify`** sees only the `ai_description` (a full "assign ONLY when… do NOT
  assign when…" definition) → the model judges meaning.

| Sentence | Rule engine | `ai_classify` |
|---|---|---|
| *"I'm totally lost on what you just explained."* | **misses** (no keyword) | **catches** (understands it's confusion) |
| *"I do not know."* | correctly ignores | can **over-tag** (semantic drift) |

**Bottom line: same taxonomy, two engines.** Rules = deterministic keyword matching
(consistent, brittle to new phrasing); AI = semantic matching (handles paraphrase, needs
validation, costs per call). Because `ai_classify` runs on the label's **`ai_description`**
(plus the global, category-agnostic `INSTRUCTIONS` preamble in `ai_classify.py`), **that
wording is where all of its quality lives** — loosening or tightening it moves results
dramatically. For the rule engine the descriptions are inert; the lanes do the work. See
[`../classification_rule_engine/README.md`](../classification_rule_engine/README.md) for
the matching engine.

---

## Full run vs. incremental re-tag — when to use which

`ai_classify.py` (**full**) re-scores **every** distinct sentence for **every** target
label and rebuilds the table from scratch, refreshing the `ai_result_json` audit blob.
`ai_classify_incremental.py` (**incremental**) diffs the current label **`ai_description`s**
against a saved snapshot and re-scores **only the changed/added label(s)** over a bounded
candidate set, MERGEing into an `_incremental` copy (baseline preserved) — far cheaper,
but it does **not** refresh `ai_result_json`.

**Run the FULL job when:**
- there is no baseline table / snapshot yet (first run);
- you **added a new category** — a new label can apply to *any* sentence, so all
  sentences must be scored (`narrow` can't find gains);
- you changed something **global**: the `INSTRUCTIONS` preamble, confidence threshold,
  `min_words`, scope (`classify_date` / `hour_*`), `sample_limit`, or the **model file**
  (e.g. `category_model.json` → `category_model_v2.json`);
- you changed **many** labels at once, or you need a **fresh `ai_result_json`** audit blob.

**Run the INCREMENTAL job when** all three hold: a baseline **+ snapshot** exist, you
edited **one/few** labels' `ai_description`, and you want it applied cheaply:

| Kind of `ai_description` edit | `mode` |
|---|---|
| **Tightened** a definition (only removes tags) | `narrow` — re-scores just the currently-tagged rows (cheapest) |
| **Broadened** a definition (can add tags) | `full` (all distinct) or `terms` (keyword proxy) — `narrow` can't find new gains |

The incremental diff compares against the **snapshot**, so seed it first (or force a
label with `changed_labels`) or the diff is meaningless.

### ⚠️ Keyword edits do NOT affect `ai_classify`
`ai_classify` reads `ai_description`, **never the keyword lanes**. So editing a category's
`lanes` (keywords / and / and2 / not):
- **moves the rule engine** → run the **rule-engine** incremental
  (`../classification_rule_engine/`), which diffs the lanes and, for a broadening, finds
  gain candidates by keyword;
- is **invisible to `ai_classify`** → its incremental reports "nothing to do", and a full
  `ai_classify` run returns identical tags.

To change `ai_classify`'s behavior for a category you must edit its **`ai_description`**
(then `full`/`terms` for a broadening, `narrow` for a tightening).

### Selecting the rule set (`category_model` param)
Both the full and incremental jobs take a **`category_model`** param: empty = the default
`category_model.json`; set it to another basename synced with the bundle (e.g.
`category_model_v2.json`) to run against a different rule set. The full job scores against
it; the incremental diffs its `ai_description`s against the snapshot.

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

### `ai_classify_incremental.py` — incremental re-tag (semantic diff)
The cheap analogue of the rule-engine incremental job, for `ai_classify`. Diffs the
current label **`ai_description`s** against a snapshot (`<tags>__desc_snapshot`) and
re-scores **only the changed/added label(s)** over a bounded candidate set, then MERGEs
the rebuilt label set + recomputed 0/1 columns into an `_incremental` copy (baseline
untouched). See **"Full run vs. incremental"** above for when to use it and which `mode`.
- `mode` = `full` (all distinct — correct for any change) | `narrow` (currently-tagged
  rows only — tightening/removals) | `terms` (broadening with a `candidate_terms` keyword
  proxy). `changed_labels` forces specific labels; `seed_only=1` (re)seeds the snapshot.
- Does **not** refresh `ai_result_json` (only the load-bearing `ai_labels` + columns).
  Confidence settings must match the full run. Requires `warehouse_id`.

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
`run()`: `run_ai_query_sql_notebook.py`, `run_ai_classify_notebook.py`,
`run_ai_classify_incremental_notebook.py`, and `run_compare_notebook.py` (run compare
**after** the rule job and an AI job have produced their tag tables).

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
