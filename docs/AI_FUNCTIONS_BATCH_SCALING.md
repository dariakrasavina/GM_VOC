# GM VOC — Scaling `ai_classify` Batch Inference (2.2M POC → 600M rows)

This is the requirements and best-practices spec for running AI-based text
classification at production scale on Databricks. It exists because the POC
(2.2M rows) and the full backfill (600M rows) are *different engineering
problems*: at 600M rows, decisions about deduplication, capacity, and
checkpointing decide whether the job is feasible and affordable at all.

Companion to [`LIGHTWEIGHT_AI_SOLUTION.md`](LIGHTWEIGHT_AI_SOLUTION.md) (the AI
track / Approach 2). A styled, self-contained HTML version of this same content
lives at `ai_functions_batch_scaling_guide.html` in the repo root.

> **Scope note.** Figures marked *illustrative* are placeholders to be replaced
> with POC-measured values. Verify all pricing against the live Databricks
> pricing page before quoting cost.

---

## 0. The one thing to internalize first

Every decision below follows from a single fact: **AI Functions batch inference
is priced on compute, not tokens.** When you submit a batch, Databricks
provisions GPU capacity — measured in **Model Units (MU)** — processes the job,
and scales down. You pay for MU-hours allocated over the job's duration.

```
Cost  =  Model Units provisioned  ×  price per MU-hour  ×  job duration
```

Four consequences:

1. **No universal $/row.** There is no reliable rows-per-minute or cost-per-row
   figure to look up — it depends on your data and output size. You **must
   benchmark** to get real numbers.
2. **Faster ≈ same cost.** Raising the MU ceiling shortens wall-clock time at
   *roughly the same total cost* (similar MU-hours, higher parallelism).
3. **Output tokens are the lever.** Duration scales with total work, dominated
   by output tokens. Rationales and confidence text are the #1 cause of blown
   estimates.
4. **Do less work.** The cheapest classification is the one you don't run. At
   600M rows, **deduplication is the dominant cost lever** (see §2).

### The diagnostic that matters most

Check the run's billing / query metadata:

- `COMPUTE_TIME` → you are on the **batch / AI Functions** path (capacity-based;
  this guide applies).
- `TOKEN` → you slipped onto the **PPT / FMAPI** path (token-based). A slow
  `TOKEN` run is a **capacity investigation, not warehouse tuning**.

Do **not** stand up a customer-managed Provisioned Throughput endpoint first —
AI Functions does not run on PT-endpoint compute.

---

## 1. Hard requirements

`[MUST]` blocks execution or correctness · `[SHOULD]` strongly recommended ·
`[AVOID]` known anti-pattern.

| ID | Priority | Requirement |
|----|----------|-------------|
| REQ-01 | MUST | **Runtime & engine floor.** DBR **16.1+** (never below 15.4 LTS). Serverless / SQL Warehouse — **not SQL Classic**. `ai_classify` cannot be applied through a **View**. |
| REQ-02 | MUST | **Pin `ai_classify` v2.1.** Set `version='2.1'`. v2.1 returns a `VARIANT` — `{response:[{value, confidence_score}], metadata, error_message}` — differing from v2.0 (plain strings) and v1.0 (single string). Downstream parsing reads `.response[*].value`. |
| REQ-03 | MUST | **Label ceiling.** 2–500 labels (v2.x), each 1–100 chars, content ≤ 1M tokens. Above ~500 categories, use **vector_search + LLM** instead. |
| REQ-04 | MUST | **Deduplicate before classifying.** At 600M this is the architecture, not an optimization. Classify the DISTINCT normalized text; join labels back. See §2. |
| REQ-05 | MUST | **Checkpoint the backfill.** Use Structured Streaming with `trigger(availableNow=True)` + a checkpoint location so a failure at row 400M resumes instead of re-running (and re-paying) from zero. |
| REQ-06 | SHOULD | **Rationales OFF in prod.** Keep `enableRationales` / `enableConfidenceScores` for QA only — in prod they inflate output tokens → longer duration → more MU-hours. |
| REQ-07 | SHOULD | **Prefer the managed model; benchmark before switching.** Task-specific `ai_classify` uses a Databricks-managed model (no choice) — the intended path. If model selection is mandatory, move to `ai_query` and test `gpt-oss-120b` first, then `llama-4-maverick`. Do **not** promise Sonnet-equivalent quality without a representative eval. |
| REQ-08 | AVOID | **Manual micro-batching / parallel notebooks.** Shows up as a spiky MU-utilization graph and *reduces* throughput. Submit one large set-based query and let the platform parallelize. |
| REQ-09 | INFO | **Governance is covered.** Data stays in-workspace/region, never used for training, transient logs only. HIPAA on all clouds; PCI/FedRAMP via Compliance Security Profile. UC lineage/audit apply. Pin the output schema version for compliance. |

---

## 2. The dedup lever — why 600M is really far fewer rows

**`ai_classify` does not deduplicate for you.** It runs one inference per row
you submit — the debug dashboard's own health check is *"total inferences ≈ row
count."* Identical text is re-inferred (and re-billed) every time. VOC text is
highly repetitive (canned responses, short common phrases, template fragments),
so classifying all 600M rows means paying many times over for the same answer.

**Measure the distinct ratio on the 2.2M POC**, then classify only distinct
normalized text and join labels back to every row.

Illustrative leverage at 600M (replace ratio with your POC-measured value):

| Distinct ratio (measured) | Distinct rows to classify | Work vs. naïve | Implication |
|---|---:|---:|---|
| 10% (very repetitive) | 60,000,000 | 10× less | Dedup is the difference between feasible and not |
| 25% | 150,000,000 | 4× less | Large saving; still needs streaming + MU increase |
| 50% | 300,000,000 | 2× less | Worthwhile; verify normalization is aggressive enough |
| 90% (near-unique) | 540,000,000 | 1.1× less | Little help — plan for full-scale cost |

Normalization quality drives the ratio: lower-case, trim, collapse whitespace,
consider stripping punctuation/IDs before `DISTINCT`. **Normalize identically on
both sides of the join.**

```sql
-- 1) Classify only DISTINCT normalized text — the dominant cost lever at 600M
CREATE OR REPLACE TABLE voc.distinct_labeled AS
SELECT
  text_norm,
  ai_classify(
    text_norm,
    labels,                                  -- array, or {label: description} map
    map('version','2.1', 'multilabel','true')   -- rationales OFF in prod
  ) AS classification
FROM (SELECT DISTINCT lower(trim(text)) AS text_norm FROM voc.raw_600m);

-- 2) Join labels back to all 600M rows (cheap — no inference here)
CREATE OR REPLACE TABLE voc.labeled AS
SELECT r.*, d.classification
FROM voc.raw_600m r
JOIN voc.distinct_labeled d ON lower(trim(r.text)) = d.text_norm;
```

**Confirm the saving is real:** after Stage 1, compare *total inferences* (debug
tab) against *distinct row count*. If inferences track the raw count, the dedup
is yours to own and the 4×–10× saving is genuine money.

---

## 3. The staged path from POC to 600M

Do not extrapolate from a 100-row test — the system needs volume to reach
steady-state throughput, so small tests always feel slow. Pass each gate only
when its exit criteria are met.

### Stage 0 — Calibrate (50K–100K distinct rows)
- Confirm labels, descriptions, prompt/instructions.
- Rationales **ON** — inspect quality on a sample; record avg **output tokens**/row.
- Confirm metadata shows `COMPUTE_TIME`.
- **Exit when** labels & output shape are locked and token size is known.

### Stage 1 — Prove (2.2M rows)
- Measure the **distinct ratio** (§2).
- Rationales **OFF** (prod config); QA sample separately.
- Capture MU-hours, duration, success rate from `go/batchinference/debug`.
- Quality eval on a labeled sample.
- **Exit when** quality passes and per-distinct-row MU-hours is measured.

### Stage 2 — Project (math, not compute)
- Compute 600M distinct set = 600M × ratio.
- Estimate MU-hours & cost (§4); decide MU ceiling for target duration.
- File `go/batchlimitincrease` if needed.
- **Exit when** cost/duration are approved and capacity is granted.

### Stage 3 — Execute (600M rows)
- Structured Streaming + checkpoint (resume-safe); `failOnError => false`.
- Monitor the MU graph live; then run incrementally for new data.
- Verify billing lands under `AI_FUNCTIONS`.
- **Exit when** all rows labeled at ≥95% success and cost matches projection.

> **Throughput reality.** `ai_classify` needs ~**25K–50K+ rows** to reach
> steady-state throughput; the first minutes are ramp-up. At 600M you are far
> past that floor — the risk is the MU ceiling and output-token size, not batch
> size. Benchmark at 50K–100K, never at 100 rows.

---

## 4. Estimating cost & sizing capacity

The only honest estimate comes from measuring at POC scale and scaling by the
distinct row count:

1. From Stage 1, read **MU-hours consumed** and **distinct rows processed**
   (D_poc) off the debug dashboard.
2. Per-row rate = MU-hours_poc ÷ D_poc — the number that travels.
3. Target distinct set D_prod = 600M × measured distinct ratio.
4. Estimated MU-hours_prod ≈ per-row rate × D_prod (roughly linear in work).
5. Estimated cost ≈ MU-hours_prod × price per MU-hour.
6. Estimated duration ≈ MU-hours_prod ÷ MU ceiling — raising the ceiling
   shortens the run at ~constant cost.

> **Estimate-buster.** Linear scaling only holds if **output tokens per row are
> stable**. If rationales were on during the POC but off in prod (or vice-versa),
> the per-row rate is wrong. Lock the exact prod config *before* the Stage-1
> measurement.

**Model Unit capacity:**

| Lever | Default | Effect of raising it |
|---|---|---|
| MU per session | 1,000 | Higher parallelism → shorter duration, ~same total cost |
| MU per workspace | 4,000 | Ceiling across concurrent jobs; increase via `go/batchlimitincrease` |
| Batch size | whole dataset | Bigger = better steady-state throughput (never micro-batch) |

**Billing buckets:**

| Function | Billing product |
|---|---|
| `ai_classify` / `ai_extract` / `ai_parse_document` | `AI_FUNCTIONS` (this project) |
| `ai_query` | `MODEL_SERVING` / `BATCH_INFERENCE` (only if model selection) |

Public list figures for `ai_classify` (budget tier ~3–6 DBU/1k docs vs. standard
tier ~40–60 DBU/1k docs) are an order-of-magnitude sanity check only — your own
benchmark is the source of truth. Third-party batch APIs
(OpenAI/Anthropic/Bedrock) can be ~50% cheaper per token, but this path wins on
TCO (no infra), OSS models, and native Delta/UC integration.

---

## 5. The execution pattern for the backfill

Wrap the distinct-set classification in Structured Streaming so it checkpoints —
a crash at row 400M of the distinct set restarts there, not at zero.

```python
# Structured Streaming with AI Functions — resume-safe backfill
from pyspark.sql import functions as F

stream = spark.readStream.table("voc.distinct_text")   # DISTINCT normalized text

labeled = stream.selectExpr(
    "*",
    """ai_classify(text_norm, labels,
                   map('version','2.1','multilabel','true')) AS classification""",
)

(labeled.writeStream
    .trigger(availableNow=True)                     # process all available, then stop
    .option("checkpointLocation", "/Volumes/voc/_chk/classify")
    .toTable("voc.distinct_labeled"))

# If it fails partway, just re-run — the checkpoint skips finished rows.
```

Keep everything at DataFrame/set level (never row-by-row loops). Use
`failOnError => false` so a few bad rows don't kill a multi-hour job — inspect
the error column afterward.

---

## 6. Observability & troubleshooting

`go/batchinference/debug` is the single most useful diagnostic. Find the Batch
Run ID (workspace ID + time range → "Find Batch Run ID"), then read the MU
utilization graph.

**MU-utilization graph patterns:**

| Pattern | Meaning | Action |
|---|---|---|
| Ramps up, stays high | Healthy — at the ceiling | Need more speed? Request MU increase |
| Flat at the ceiling | At max limit — throughput-capped | `go/batchlimitincrease` |
| Stays low the whole time | Low demand or errors in data | Check inputs / error column |
| Spikes up and down | Mini-batching (anti-pattern) | Consolidate into one large query |

**Debug-tab health thresholds:**

| Metric | Expected | If not |
|---|---|---|
| Total inferences | ≈ row count (slightly higher = retries) | Far off → inspect input |
| Success rate | ≥ 95% | Examine error types |
| Avg MU utilization | ≥ 70% | Output tokens too high — trim |
| Output tokens | Reasonable for task | High → drop rationales / maxTokens |

**Common errors:**

| Error | Fix |
|---|---|
| `429 Rate Limit` | Check dashboard; request MU increase |
| `INTERNAL_ERROR` | `failOnError => false` + retries |
| `PERMISSION_DENIED` | Grant admin access / budget policy |
| `PROHIBITED_CONTENT` | Sanitize input (or switch model on `ai_query`) |
| `ENDPOINT_NOT_FOUND` | Enable cross-geo routing |

**Escalation.** Quality problems (wrong labels) → research team via the
`#apa-ai-functions` workflow. Bugs / performance → ES ticket
(`go/aifunction/incident`) + `#apa-ai-functions`. Include function, sample input,
expected vs. actual, workspace ID, and batch run ID.

---

## 7. Pre-flight checklist for the 600M run

- [ ] Distinct ratio measured on the 2.2M POC; join keys normalized identically on both sides.
- [ ] Prod config frozen: `version='2.1'`, multilabel setting fixed, rationales OFF, confidence per requirement.
- [ ] Per-distinct-row MU-hours captured; cost & duration projected and approved.
- [ ] MU limit increase filed and granted if the target duration needs it (`go/batchlimitincrease`).
- [ ] Structured Streaming checkpoint location provisioned; job is idempotent / resume-safe.
- [ ] Quality eval passed on a representative labeled sample; output schema version pinned.
- [ ] Metadata confirmed as `COMPUTE_TIME` (batch); billing lands under `AI_FUNCTIONS`.
- [ ] `failOnError => false` set; error column will be reviewed after the run.

---

## References

- ai_classify — <https://docs.databricks.com/aws/en/sql/language-manual/functions/ai_classify>
- AI Functions overview — <https://docs.databricks.com/aws/en/large-language-models/ai-functions>
- Public pricing — <https://www.databricks.com/product/pricing/ai-functions>
- Internal FAQ — `go/aifunctions-faq` · Debug — `go/batchinference/debug` · Limit increase — `go/batchlimitincrease` · Channel — `#apa-ai-functions`
