# Databricks notebook source
# COMMAND ----------
# GM VOC — ai_classify via the BUILT-IN ai_classify() function (single-label),
# run as DBSQL batch on a SQL warehouse. Orchestrates only; the classification
# runs on the warehouse. Requires a warehouse_id parameter.
import os
import sys

for cand in (os.getcwd(), "/Workspace" + os.getcwd()):
    if cand and cand not in sys.path:
        sys.path.insert(0, cand)

# COMMAND ----------
import ai_classify as job
job.run()
