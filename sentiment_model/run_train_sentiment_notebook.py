# Databricks notebook source
# COMMAND ----------
# GM VOC — Track 3: train the transformer sentiment model (MLflow).
# Requires Databricks Runtime for ML (GPU recommended) + serverless/Model-Serving
# access for the ai_query weak-labeling step.
# Install libs not already on the runtime. On serverless compute PyTorch is NOT
# pre-installed (unlike DBR ML), and the HuggingFace Trainer needs torch +
# accelerate — include them explicitly (this was the 'No module named torch' fail).
%pip install -q torch accelerate transformers datasets evaluate vaderSentiment
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
