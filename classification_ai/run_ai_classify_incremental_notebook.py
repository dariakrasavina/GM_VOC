# Databricks notebook source
# COMMAND ----------
# GM VOC — INCREMENTAL ai_classify re-tag after a label-DESCRIPTION change.
# Re-scores only the changed label(s) over a bounded candidate set and MERGEs the
# result into the existing ai_classify tags table in place (vs a full re-run).
# Orchestrates only; the ai_classify() inference runs on the SQL warehouse.
# Requires a warehouse_id parameter. See ai_classify_incremental.py for the
# mode = full | narrow | terms candidate strategies.
import os
import sys

for cand in (os.getcwd(), "/Workspace" + os.getcwd()):
    if cand and cand not in sys.path:
        sys.path.insert(0, cand)

# COMMAND ----------
import ai_classify_incremental as job
job.run()
