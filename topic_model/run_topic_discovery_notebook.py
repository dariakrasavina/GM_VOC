# Databricks notebook source
# COMMAND ----------
# GM VOC — unsupervised topic discovery entrypoint (embeddings + KMeans + ai_gen).
# Requires serverless compute + DBR 18.2+ and Spark MLlib (built in).
import os
import sys

for cand in (os.getcwd(), "/Workspace" + os.getcwd()):
    if cand and cand not in sys.path:
        sys.path.insert(0, cand)

# COMMAND ----------
import topic_discovery_job as job
job.run()
