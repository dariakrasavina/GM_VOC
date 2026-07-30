# GM VOC POC — Databricks Topic Model

Replicates GM's Qualtrics **XM Discover** topic tagging over contact-center
audio transcripts, on Databricks. Built for the Phase 2 POC: prove Databricks
can reproduce the swim-lane rule logic and produce tagged output that validates
directly against the XM Discover control set.

## Design

Two clean layers so the exact same matching logic runs locally and at scale:

| File | Role | Depends on |
|------|------|------------|
| `rule_engine.py` | XM Discover query language: parser + evaluator (OR/AND/NOT, phrases, wildcards `* ?`, fuzzy `~`, proximity `"…"~N`, `attr:value`). | stdlib only |
| `tagger.py` | Applies the global scope filter + the topic nodes to one sentence. | `rule_engine` |
| `rules.json` | The topic hierarchy + lane rules, **generated from the source workbook**. | — |
| `build_rules_config.py` | Regenerates `rules.json` from `requirements/Qualtrics_Parent_and_Leaf_Nodes.xlsx`. | stdlib only |
| `run_local.py` | Local driver over the sample CSVs (no cluster needed). | `tagger` |
| `voc_topic_model_job.py` | **Databricks/PySpark job** — reads Delta tables, tags via `pandas_udf`, writes Delta output + aggregates. | pyspark + `tagger` |
| `build_dashboard.py` | Renders the HTML review dashboard from run outputs. | stdlib only |
| `test_rule_engine.py` | 33 unit tests covering every documented syntax rule. | `rule_engine` |
| `make_demo_fixture.py` | Schema-identical demo data (the real sample has no topical content). | stdlib only |

`rule_engine.py` and `tagger.py` **never import pyspark**, which is exactly why
they can run inside a Spark `pandas_udf` on the cluster *and* be unit-tested on a
laptop with no JVM.

## The 4 POC topics (comparison targets)

From two branches of the APM hierarchy, encoded verbatim from the workbook:

1. Contact Center → Dissatisfied With Advisor → **Confusing/Makes No Sense**
2. Contact Center → Dissatisfied With Advisor → **Inaccurate Information**
3. Loyalty → Rewards → **Points**
4. Loyalty → Rewards → Points → **Redeem**

Each node combines four lanes: `keywords` (OR seed) **AND** `and` **AND** `and2`
**AND NOT** `not`. A global "DBX POC" filter scopes to English + audio +
customer-side verbatims and strips IVR/boilerplate before any topic is applied.

## Data

- Join: `sentence.natural_id == metadata.natural_id` (verified 1:1 on the sample;
  `id_document == case_id == document_id` is an equivalent key).
- Grain: sentence-level (`words` column).
- Date range: `document_date` in 2025-07-01 … 2026-06-30.

## Run locally

```bash
# regenerate rules from the workbook (only if the workbook changed)
python3 topic_model/build_rules_config.py

# unit tests
python3 topic_model/test_rule_engine.py

# run over the provided sample CSVs
python3 topic_model/run_local.py --out outputs/

# demo run that actually tags (schema-identical realistic verbatims)
python3 topic_model/make_demo_fixture.py
python3 topic_model/run_local.py \
  --sentences test_data/demo_sentence_level.csv \
  --metadata  test_data/demo_metadata.csv --out outputs_demo/
python3 topic_model/build_dashboard.py --out outputs_demo/
```

> **Note on the provided sample data:** `test_data/*sample_data.csv` is synthetic
> OnStar roadside dialogue — only ~40 distinct sentences, none containing any of
> the four topics' vocabulary. A real run over it therefore (correctly) tags
> **0** sentences. Use the demo fixture to see tagging exercised end-to-end.

## Run on Databricks

1. Put `rule_engine.py`, `tagger.py`, `rules.json` on the executor path
   (Repos on `sys.path`, `--py-files`, or a wheel).
2. Set the table names / date range at the top of `voc_topic_model_job.py`
   (or wire them to job widgets).
3. Run as a notebook or job task. Outputs:
   - `…voc_topic_tags` — one row per sentence, one 0/1 column per topic plus a
     `<topic>__terms` explanation column (the matched rule terms = "chicklets").
   - `…voc_topic_frequencies` — sentence + document counts per topic.

## Extending

Add or edit topics in the workbook and re-run `build_rules_config.py`, or hand-edit
`rules.json`. No engine code changes are needed to add nodes.
