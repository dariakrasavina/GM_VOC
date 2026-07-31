# VOC POC — Validation Summary

Deliverable for POC Phase 2/3: what was built, what the data showed, and the
strengths / gaps / recommendations for comparing Databricks against the XM
Discover control set.

## What was built

A rule-based topic-tagging engine that faithfully implements GM's XM Discover
Designer query language and applies the four selected APM nodes at sentence
grain. It runs both locally (stdlib) and on Databricks (PySpark `pandas_udf`)
from a single shared code path, so local validation and production produce
identical tags. Tags are stored per sentence as one column per topic plus a
matched-terms explanation column, mirroring XM Discover attribute storage for
direct 1:1 validation against the control set.

## Rule fidelity — verified

The engine passes 33 unit tests covering every documented syntax feature:
implicit OR, comma-OR, Boolean AND/OR/NOT (infix and prefix), parentheses
grouping, quoted exact phrases, single/multi wildcards (`?`, `*`), fuzzy (`~`),
proximity (`"phrase"~N` with move/swap semantics), and attribute search
(`_id_source:audio`, `language:english`, `cc_lob_mv:*reward*`, …).

On a schema-identical demo fixture, all four nodes tagged correctly, including
the hard cases that distinguish a faithful replication from a naive keyword grep:

| Verbatim | Tagged | Why (correct) |
|---|---|---|
| "I am so confused by what the advisor told me" | Confusing | keyword `confused` |
| "no confusion here everything was clear" | — | `not` lane excludes `"no confusion"` |
| "I apologize for the confusion" (agent) | — | scope filter drops agent-side |
| "gave me incorrect information about my warranty" | Inaccurate | `incorrect` + `information` lanes |
| "wrong directions to the dealership" | — | `not` lane excludes `the dealership` |
| "he wants to redeem his points" | Points (not Redeem) | Redeem `not` lane excludes `"he wants to redeem"` |
| "quiero canjear mis puntos" (Spanish) | — | scope filter: English only |

## What the provided sample data showed

The two sample CSVs (`test_data/*sample_data.csv`) are **synthetic OnStar
roadside-assistance dialogue**, not representative VOC content:

- 100,000 sentence rows but only **~40 distinct sentences / ~103 unique words**.
- **Zero** occurrences of any of the four topics' vocabulary
  (`confus`, `redeem`, `points`, `reward`, `incorrect`, `wrong`, `inaccur`,
  `voucher`, `loyalty`) anywhere in the file.
- Distributions that *are* usable for plumbing validation: `id_source` = 100%
  audio; language split EN 91.8k / ES 5.1k / FR 2.1k / DE 1.1k; verbatimtype
  split agent 52.9k / client 47.1k.

Consequences:
- The global scope filter behaves correctly: **43,241 / 100,000 (43.2%)** rows
  pass (English + audio + customer-side + non-boilerplate).
- Topic tagging correctly returns **0** matches — there is no topical content to
  tag. This validates the join, filter, ordering, and output plumbing, but **not**
  tagging accuracy, which requires real verbatims.

## Throughput (local, indicative only)

Local single-process run: 100,000 sentences in ~10s (~10k sentences/sec) on the
laptop, pure Python. This is a floor, not the production number: the Spark job
parallelizes the same UDF across the cluster. GM's ~4,000 calls/hour planning
volume (≈ tens of thousands to a few hundred thousand sentences/hour) is well
within range; a proper cost/throughput benchmark needs a real Databricks run on
representative data (see below).

## Strengths

- **Faithful replication**, not approximation — the dense `not`-lane exclusion
  logic that drives XM Discover precision is fully honored.
- **Deterministic** — same input → same output every run, which GM requires for
  trend reporting. No model drift, no sampling.
- **Explainable** — every tag carries the exact matched terms ("chicklets"), so a
  reviewer can see *why* a sentence was or wasn't tagged.
- **Rules are data**, generated from the source workbook — the text-analytics
  team can extend nodes without touching engine code.
- **One code path** local ↔ Spark eliminates "works locally, differs in prod".

## Gaps / open items

1. **Need real, representative sample data** to actually benchmark tagging
   accuracy against the XM Discover control set. The current sample cannot do this.
2. **Sentence ordering**: Andrew's multi-field ranking logic should replace the
   `document_date, sentence_id, id_verbatim` proxy used for sequencing — matters
   for any cross-sentence / proximity-at-document scope.
3. **Proximity move semantics**: implemented as a sliding-window span consistent
   with the docs; worth spot-checking against XM Discover on a handful of live
   proximity rules to confirm identical edge-case behavior.
4. **Fuzzy (`~`) edit distance** uses a length-scaled default (0/1/2); confirm
   this matches XM Discover's exact fuzzy threshold.
5. **Parent Doc / Verbatim / Other-Verbatim lanes** exist in the workbook schema
   but are empty for these four nodes; not yet exercised.
6. **Cost benchmark** (linear vs. exponential scaling) and the ~6-hour rerun
   repeatability test are pending a real Databricks run.

## Recommended next steps

1. Get a real, de-identified sample (a few days of in-scope audio) into the POC
   schema and rerun `voc_topic_model_job.py`.
2. Join Databricks `voc_topic_tags` to the XM Discover control tags on
   `id_verbatim` and produce a confusion matrix per node (precision/recall/F1).
3. Tune only where regressions appear; because rules are explainable, each
   mismatch points at a specific lane term.
4. Run the rerun-after-rule-change benchmark and capture DBU cost + wall-clock at
   the target volume for the cost/throughput story.
