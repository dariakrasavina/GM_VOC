# Databricks notebook source
# COMMAND ----------
# GM VOC — AI classification entrypoint (ai_classify + ai_analyze_sentiment).
# Requires serverless compute + DBR 18.2+.
import os
import sys

for cand in (os.getcwd(), "/Workspace" + os.getcwd()):
    if cand and cand not in sys.path:
        sys.path.insert(0, cand)

# COMMAND ----------
import ai_classify_job as job
job.run()
