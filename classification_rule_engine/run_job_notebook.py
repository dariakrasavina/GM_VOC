# Databricks notebook source
# COMMAND ----------
# GM VOC Topic Model — job entrypoint notebook.
# Deployed by the Asset Bundle; imports the engine from the same folder and runs
# the PySpark tagging job. Config comes from job/base parameters (widgets).
import os
import sys

# The bundle uploads the track folder next to this notebook; make it importable.
_here = os.path.dirname(os.path.abspath("__file__")) if "__file__" not in dir() else os.path.dirname(__file__)
for cand in (_here, os.getcwd(), "/Workspace" + os.getcwd()):
    if cand and cand not in sys.path:
        sys.path.insert(0, cand)

# COMMAND ----------
import voc_topic_model_job as job

# run() resolves parameters from widgets (set via the job's base_parameters),
# then applies the global scope filter + the four topic nodes and writes the
# tagged Delta table + frequency table.
job.run()
