# Databricks notebook source
# COMMAND ----------
# GM VOC — Track 3: score verbatims with the registered transformer model +
# the VADER lexicon baseline.
# torch + transformers are needed to load/run the registered transformer model
# on serverless compute (not pre-installed there). vaderSentiment for the baseline.
%pip install -q torch transformers vaderSentiment
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
