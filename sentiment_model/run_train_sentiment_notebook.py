# Databricks notebook source
# COMMAND ----------
# GM VOC — Track 3: train the transformer sentiment model (MLflow).
# Runs on a single-node GPU cluster with Databricks Runtime for ML (GPU), where
# torch + transformers are PRE-INSTALLED — so we do NOT pip-install torch here
# (installing a CPU torch over the runtime's GPU build breaks CUDA). We only add
# the few small extras the ML runtime may not ship.
%pip install -q evaluate vaderSentiment
dbutils.library.restartPython()

# COMMAND ----------
import os
import sys
for cand in (os.getcwd(), "/Workspace" + os.getcwd()):
    if cand and cand not in sys.path:
        sys.path.insert(0, cand)

# COMMAND ----------
import train_sentiment_model as job
job.run()
