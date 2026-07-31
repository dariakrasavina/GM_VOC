# Databricks notebook source
# COMMAND ----------
# GM VOC — rule-vs-AI agreement analysis entrypoint.
# Reads voc_topic_tags (rules) and voc_ai_topic_tags (AI); writes comparison.
import os
import sys

for cand in (os.getcwd(), "/Workspace" + os.getcwd()):
    if cand and cand not in sys.path:
        sys.path.insert(0, cand)

# COMMAND ----------
import compare_approaches_job as job
job.run()
