# GM VOC POC — Lightweight AI-Powered Solution (and approach comparison)

The POC evaluates whether Databricks can replicate *and* improve on GM's
Qualtrics XM Discover topic tagging. There is no single "right" method, so we
built three approaches, from most-deterministic to most-AI, and a job that
compares them. Track 2 — the **lightweight AI-powered solution** built on
Databricks AI functions — is the focus of this document.

| # | Approach | Files | What it is | Strengths | Trade-offs |
|---|----------|-------|-----------|-----------|-----------|
| 1 | **Rule engine** | `rule_engine.py`, `tagger.py`, `voc_topic_model_job.py` | Faithful re-implementation of GM's XM Discover swim-lane rules | Deterministic, explainable, zero model cost, exact control-set replica | Manual rule upkeep; misses novel phrasing; brittle to wording |
| 2 | **AI classification** | `ai_classify_job.py` (`ai_classify` + `ai_analyze_sentiment`) | An LLM assigns each sentence to a topic, steered by the topic's business definition; sentiment attached | No keyword maintenance; handles paraphrase/synonyms; adds sentiment | Non-deterministic; per-call cost; needs validation vs. control |
| 3 | **Topic discovery** | `topic_discovery_job.py` (embeddings + KMeans + `ai_gen`) | Unsupervised — embeds sentences, clusters by meaning, auto-names themes | Finds themes nobody defined (the "global other" problem); no labels needed | Clusters need human interpretation; not a 1:1 control replica |
|   | **Comparison** | `compare_approaches_job.py` | Agreement analysis: rules vs. AI per topic | Quantifies overlap; surfaces AI misses & rule gaps for SME review | Uses rules as *proxy* ground truth, not GM's true control |

## Why all three (and the maintenance point)

This is a **lightweight AI-powered solution**, not a custom-trained ML model.
Databricks' built-in AI functions (`ai_classify`, `ai_analyze_sentiment`,
`ai_query`, `ai_gen`) call **hosted** models — there is nothing to train, host,
or retrain, so GM would not own long-term model maintenance. That directly
addresses the concern that a bespoke ML approach leaves the team responsible for
upkeep.

To be precise about what is and isn't ML here:
- **Classification (Approach 2)** is *not* a trained classifier — it is a hosted
  LLM (`ai_classify`). No weights are learned on GM data.
- **Topic discovery (Approach 3)** *does* train a real model: Spark MLlib KMeans
  is fit on sentence embeddings. That is genuine unsupervised learning.
- A custom **supervised** classifier (e.g. embeddings + MLlib logistic
  regression tracked in MLflow) is deliberately **out of scope** for this
  lightweight solution; it can be added later as a separate track if the POC
  wants a hostable, zero-token-cost model.

Approach 1 remains the deterministic control the POC regresses against;
Approach 3 is the discovery capability rules can't provide.

## How the AI track works

**AI classification (`ai_classify_job.py`)**
1. Scope to English + audio + customer-side verbatims in the date range.
2. `ai_classify(words, labels_json, options)` where `labels_json` maps each of
   the four POC topics (plus a "None of these" catch-all) to its business
   definition pulled from the *same* `rules.json` the rule engine uses — so both
   tracks classify against identical topic definitions.
3. `ai_analyze_sentiment(words)` adds positive/negative/neutral/mixed.
4. Writes `voc_ai_topic_tags` (topic + confidence + sentiment per sentence).

**Topic discovery (`topic_discovery_job.py`)**
1. Scope as above.
2. Embed each sentence: `ai_query('databricks-gte-large-en', words)` → vector.
3. Spark MLlib **KMeans** clusters the vectors into emergent themes.
4. `ai_gen` auto-names each cluster from its representative verbatims.
5. Writes `voc_discovered_themes` + `voc_theme_assignments`.

**Comparison (`compare_approaches_job.py`)**
Joins rule tags and AI tags on `id_verbatim`; per topic reports agreement,
rule-only, AI-only, and AI precision/recall/F1 using the rule engine as a proxy
control. Writes `voc_approach_comparison`.

## Requirements & cost

- **Serverless compute + Databricks Runtime 18.2+**, in a Model-Serving-supported
  region. AI functions do **not** run on classic clusters or Pro/Classic SQL
  warehouses. The bundle already targets serverless.
- AI functions are **pay-per-token**. Both AI jobs default to a `sample_limit`
  (5,000 / 3,000 sentences) to keep POC cost bounded; set to `0` for the full
  corpus once cost is understood.

## Running

```bash
databricks bundle deploy -t sandbox -p daria_k_sandbox

# Track 1 (rules) — produces the control tags the comparison needs
databricks bundle run voc_topic_model_job -t sandbox -p daria_k_sandbox

# Track 2 (AI classify + discovery + compare)
databricks bundle run voc_ai_pipeline_job -t sandbox -p daria_k_sandbox
```

## Output tables (all in `daria_krasavina.gm_voc`)

| Table | Produced by | Contents |
|-------|-------------|----------|
| `voc_topic_tags` | rules | per-sentence topic flags + matched terms |
| `voc_topic_frequencies` | rules | topic counts |
| `voc_ai_topic_tags` | AI classify | per-sentence AI topic + confidence + sentiment |
| `voc_discovered_themes` | discovery | emergent themes with names/summaries |
| `voc_theme_assignments` | discovery | sentence → theme_id |
| `voc_approach_comparison` | compare | rules-vs-AI agreement per topic |

## Caveats to verify on first real run

- The `ai_classify` v2.x return shape (struct vs. string) is runtime-sensitive;
  `ai_classify_job.py` extracts defensively and falls back to raw output.
- The embedding `ai_query` return (array vs. JSON envelope) should be checked on
  one row before scaling; a parse fallback is noted in `topic_discovery_job.py`.
- Confirm the embedding/gen endpoint names exist in your workspace region.
- On the provided synthetic sample data, AI results will be as thin as the rule
  results — real representative verbatims are needed for a meaningful benchmark.
