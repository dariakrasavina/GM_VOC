# Databricks notebook source
# COMMAND ----------
# GM VOC — Track 3: score verbatims with the registered transformer model +
# the VADER lexicon baseline.
# Runs on the GPU ML runtime where torch + transformers are pre-installed; only
# add vaderSentiment (the baseline) which the runtime does not ship.
%pip install -q vaderSentiment
dbutils.library.restartPython()

# COMMAND ----------
import os
import sys
for cand in (os.getcwd(), "/Workspace" + os.getcwd()):
    if cand and cand not in sys.path:
        sys.path.insert(0, cand)

# COMMAND ----------
import score_sentiment as job
job.run()
