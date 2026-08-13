# Deploying to a New (Client) Workspace

Nothing workspace-specific is committed to `databricks.yml`. You supply
everything at deploy time — the **host comes from your auth profile** and the
**catalog/schema/table names are passed with `--var`**. No file edits, no code
changes.

## 1. What you supply at deploy time

| Value | How it's supplied | Example |
|---|---|---|
| workspace **host** | the auth profile you pass with `-p` (or `DATABRICKS_HOST` env var) | `https://adb-1234567890.11.azuredatabricks.net` |
| `catalog` | `--var` | `client_marketing` |
| `schema` | `--var` | `voc` |
| `sentence_table_name` | `--var` | `audio_transcripts_sentence_level` |
| `metadata_table_name` | `--var` | `audio_metadata` |

## Source vs. target location

The bundle separates **where inputs are read** from **where outputs are written**:

| Variable | Controls | Default |
|---|---|---|
| `catalog` / `schema` | **SOURCE** — the input transcript + metadata tables | `daria_krasavina` / `gm_voc` |
| `target_catalog` / `target_schema` | **TARGET** — all output tables + the registered model | **the source** (`${var.catalog}` / `${var.schema}`) |

Because `target_*` **defaults to the source**, if you don't set it, outputs land
in the same catalog/schema as the inputs. Set `target_catalog` / `target_schema`
(via `--var`) to write outputs to a **separate schema** while still reading from
the source. Example:

```bash
--var="catalog=src_cat,schema=src_sch,\
target_catalog=out_cat,target_schema=voc_results,\
sentence_table_name=transcripts,metadata_table_name=metadata"
```
→ reads `src_cat.src_sch.transcripts`, writes `out_cat.voc_results.voc_*`.

All **output** tables are created automatically as
`<target_catalog>.<target_schema>.voc_*` — you never name them individually.

## 2. What you may need to override (region/cloud-dependent)

Add these under the `client` target's `variables:` block only if the defaults
don't fit the client environment:

| Variable | Default | Override when |
|---|---|---|
| `classify_endpoint` / `gen_endpoint` / `label_endpoint` | `databricks-meta-llama-3-3-70b-instruct` | the client region offers different foundation-model endpoints |
| `embedding_endpoint` | `databricks-gte-large-en` | same |
| `gpu_node_type` | `Standard_NC4as_T4_v3` (Azure T4) | different cloud/region, or no T4 quota (e.g. AWS `g4dn.xlarge`, `g5.xlarge`) |
| `gpu_spark_version` | `15.4.x-gpu-ml-scala2.12` | the workspace has a different LTS ML-GPU runtime |
| `ai_sample_limit` / `sentiment_sample_limit` | `200` / `300` | scaling past the POC (set `0` for the full corpus — costs more) |
| `date_start` / `date_end` | the POC window | different analysis window |

### Non-Azure clouds
The sentiment GPU cluster uses `azure_attributes`. For **AWS/GCP**, replace that
block in the `voc_sentiment_model_job` cluster spec with `aws_attributes` /
`gcp_attributes` and set a matching `gpu_node_type` (e.g. AWS `g5.xlarge`).

## 3. Prerequisites in the client workspace

- **Unity Catalog** enabled; the catalog + schema exist and the run-as identity
  can read the source tables and create tables in the schema.
- The two **source tables** loaded (sentence-level transcripts + call metadata),
  joinable on `natural_id`.
- **Serverless + DBR 18.2+** available (for the AI-function jobs).
- **Foundation Model APIs / Model Serving** enabled in a supported region (for
  `ai_classify`, `ai_query`, `ai_gen`, embeddings).
- **GPU quota** for the chosen node type (sentiment training only).
- An auth profile for the workspace (`databricks auth login -p <client-profile>`).

## 4. Deploy & run

The workspace **host is taken from the profile** (`-p`); the data locations are
passed with `--var`. Nothing in the repo needs editing.

```bash
# authenticate to the client workspace once — this establishes the HOST
databricks auth login -p <client-profile>

# deploy (pass catalog/schema/table names inline). Add target_catalog /
# target_schema only if you want outputs in a SEPARATE schema; omit them to
# write outputs alongside the source.
databricks bundle deploy -t client -p <client-profile> \
  --var="catalog=<CATALOG>,schema=<SCHEMA>,\
sentence_table_name=<SENTENCE_TABLE>,metadata_table_name=<METADATA_TABLE>,\
target_catalog=<OUT_CATALOG>,target_schema=<OUT_SCHEMA>"

# run (order matters for the comparison) — pass the same --var on run
VARS="catalog=<CATALOG>,schema=<SCHEMA>,sentence_table_name=<SENTENCE_TABLE>,metadata_table_name=<METADATA_TABLE>"
databricks bundle run voc_classification_rule_job    -t client -p <client-profile> --var="$VARS"
databricks bundle run voc_classification_ai_job      -t client -p <client-profile> --var="$VARS"
databricks bundle run voc_classification_compare_job -t client -p <client-profile> --var="$VARS"   # after the two above
databricks bundle run voc_sentiment_model_job        -t client -p <client-profile> --var="$VARS"
```

Override endpoints or the GPU node type by adding them to `--var`, e.g.
`--var="...,gpu_node_type=g5.xlarge,embedding_endpoint=databricks-bge-large-en"`.

**CI/CD alternative (no profile):** set `DATABRICKS_HOST` + credentials
(`DATABRICKS_TOKEN` or OAuth `DATABRICKS_CLIENT_ID`/`SECRET`) and `BUNDLE_VAR_*`
env vars, then run `databricks bundle deploy -t client` with no `-p`/`--var`.

## 5. Verify

```bash
databricks bundle validate -t client -p <client-profile> \
  --var="catalog=<CATALOG>,schema=<SCHEMA>,sentence_table_name=<SENTENCE_TABLE>,metadata_table_name=<METADATA_TABLE>"
# should say "Validation OK!" and use the client host from the profile
```
Check the rendered table names point at the client catalog/schema:
```bash
databricks bundle validate -t client -p <client-profile> \
  --var="catalog=<CATALOG>,schema=<SCHEMA>,sentence_table_name=<SENTENCE_TABLE>,metadata_table_name=<METADATA_TABLE>" \
  -o json 2>/dev/null \
  | python3 -c "import sys,json;d=json.load(sys.stdin);\
print(d['resources']['jobs']['voc_classification_rule_job']['tasks'][0]['notebook_task']['base_parameters'])"
```

## Notes

- The `sandbox` target (the origin dev workspace) is unchanged and still deploys
  with `-t sandbox`. The two targets are independent.
- Output tables created in the client schema:
  `voc_classification_rule_tags`, `voc_classification_rule_frequencies`,
  `voc_classification_ai_tags`, `voc_topicmodeling_themes`,
  `voc_topicmodeling_assignments`, `voc_classification_comparison`,
  `voc_sentiment_weak_labels`, `voc_sentiment_scored`, plus the registered model
  `voc_sentiment_transformer`.
- The Python jobs also have `CATALOG`/`SCHEMA` constants at the top of each file,
  used only for standalone/local runs; production always uses the bundle's
  parameters, so you normally never edit those.
