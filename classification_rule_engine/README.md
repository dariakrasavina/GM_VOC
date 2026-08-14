# Classification — Rule Engine

Deterministic classification that replicates GM's Qualtrics XM Discover category
rules. Reads GM's rule definitions and assigns each sentence to the matching
categories using keyword/boolean logic — the same answer every time, fully
explainable (you can see which words triggered each tag).

**This track uses NO machine-learning model.** It's a hand-written parser +
evaluator for GM's rule syntax. It is also the **control** the AI track is
compared against.

---

## How the pieces fit together

```
shared/category_model.json          the rules (data) — read at runtime
        │
        ▼
rule_engine.py   parses + evaluates GM's rule syntax  (pure Python, no Spark)
        │
        ▼
tagger.py        applies scope filter + all categories + hierarchy roll-up
        │
        ├──► run_local.py            run locally over CSVs (no cluster)
        └──► voc_topic_model_job.py  run on Databricks at scale (pandas_udf)
                     │
                     ▼
             run_job_notebook.py     Databricks entrypoint that calls the job
```

`rule_engine.py` and `tagger.py` **never import pyspark**, so the exact same
matching code runs locally (unit-tested) and inside the Spark job.

---

## File-by-file

### `rule_engine.py` — the query-language engine (the core)
Implements GM's XM Discover rule syntax from scratch. Two stages:

1. **Parser** (`_lex`, `_Parser`, `parse_lane`) — turns a rule string like
   `(confusion NOT "no confusion"), confused, "makes no sense", bewilder*`
   into an **AST** (tree of `Node` objects).
2. **Evaluator** — each `Node` has a `.match(ctx)` that tests a sentence:

| Node / helper | Rule syntax it handles | Logic used |
|---|---|---|
| `Or` | comma / OR list | any child matches |
| `And` | `AND` | all children match |
| `Not` | `NOT` | base matches AND exclusion does not |
| `PhraseTerm` | word, `"exact phrase"`, `wild*`, `room?`, `fuzzy~`, `"a b"~3` | token/phrase match |
| `AttrTerm` | `_verbatimtype:agentverbatim`, `language:english` | attribute-value match |
| `_make_word_matcher` | wildcards `*` `?`, fuzzy `~` | regex + edit distance |
| `_levenshtein_le` / `_fuzzy_dist` | fuzzy `~` | Levenshtein edit distance |
| `_make_phrase_matcher` | exact phrase & proximity `~N` | contiguous match / N-word window |
| `Context` | — | tokenizes the sentence once, holds attrs |
| `CompiledNode.evaluate` | one category (4 lanes) | keywords AND and AND and2 AND NOT not |

Output per category: matched (bool) + the list of matched terms ("chicklets").

### `tagger.py` — applies the model to a sentence
- `find_category_model()` / `load_rules()` — locate + load `category_model.json`
  (searches sibling `shared/` and flat cluster layouts).
- `build_ancestor_map()` — maps each category to its parents (for roll-up).
- `Tagger.in_scope()` — the global "DBX POC" filter: English + audio +
  customer-side + strips IVR/boilerplate (a big NOT-lane).
- `Tagger.tag()` — **multi-label**: returns every category a sentence matches,
  then **rolls up** to ancestor categories (a hit on Points-Redeem also marks
  Points → Rewards → Loyalty), mirroring XM Discover's category hierarchy.
- `topic_ids()` / `topic_meta()` — all categories (leaves + parents) for output.

### `voc_topic_model_job.py` — the Databricks/PySpark job
The production run. Logic in order:
1. `get_params()` — reads job params/widgets/argv (table names, dates,
   `sample_limit`).
2. `load_and_join()` — **scope-filters the sentence table in SQL first**
   (language/source/verbatimtype/date) so the slow step sees only in-scope rows,
   then left-joins the deduped metadata.
3. `make_tag_udf()` — a **`pandas_udf`** that runs `tagger.tag()` per row and
   returns JSON `{in_scope, topics:{id:[terms]}}`. *(This is the performance
   hotspot — Python matching per row.)*
4. `run()` — applies the UDF, explodes results into one 0/1 column + a
   `__terms` column per category, keeps only in-scope rows, writes
   `voc_classification_rule_tags`, and writes the per-category counts to
   `voc_classification_rule_frequencies`.

### `run_job_notebook.py` — Databricks entrypoint
Thin notebook: puts the folder on `sys.path`, imports the job, calls `run()`.

### `run_local.py` — local driver (no cluster)
Stdlib-only. Loads the sample CSVs, joins on `natural_id`, applies the **same**
`tagger`, writes `tagged_sentences.csv` + frequency/summary files. Used to
validate rule fidelity without Databricks.

### `build_dashboard.py` — HTML review dashboard
Renders topic frequencies + representative verbatims (with matched-term
chicklets) from the local run outputs.

### `test_rule_engine.py` — unit tests
33 tests covering every rule operator (OR/AND/NOT, phrases, wildcards, fuzzy,
proximity, attributes, negation edge cases). All passing.

---

## Inputs / outputs

- **Reads:** sentence table + metadata table (joined on `natural_id`), and
  `shared/category_model.json`.
- **Writes:** `voc_classification_rule_tags` (one 0/1 column + `__terms` per
  category, in-scope rows only) and `voc_classification_rule_frequencies`
  (per-category counts).

## Performance note
~99.7% of the Spark job's time is the `pandas_udf` running Python rule-matching
per row. `pandas_udf` speeds the *data transfer* (Arrow), not the *compute*
(still Python). At very large scale the real fix is porting the matching to
native Spark `rlike`/boolean expressions — see the repo-level notes. The SQL
pre-filter in `load_and_join()` reduces how many rows reach the UDF.
