# Databricks notebook source
# COMMAND ----------
# GM VOC — ai_classify via DBSQL BATCH INFERENCE.
# This notebook only orchestrates: it submits the classification SQL
# (CREATE TABLE AS SELECT ai_query(...)) to a SQL WAREHOUSE, where ai_query batch
# inference drives high concurrency to the model endpoint. The heavy work runs on
# the warehouse, not on this notebook's compute.
# Requires: warehouse_id parameter + a Model-Serving-supported region.
import os
import sys

for cand in (os.getcwd(), "/Workspace" + os.getcwd()):
    if cand and cand not in sys.path:
        sys.path.insert(0, cand)

# COMMAND ----------
import ai_classify_sql as job
job.run()
