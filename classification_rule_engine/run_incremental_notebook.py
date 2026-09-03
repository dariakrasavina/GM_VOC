# Databricks notebook source
# COMMAND ----------
# GM VOC Incremental Rule Tagging — job entrypoint notebook.
# Re-tags ONLY the sentences/columns affected by a rule change and MERGEs them
# into the existing tags table in place (no full re-tag). Config comes from the
# job's base_parameters (widgets). See incremental_rule_job.py for the approach.
import os
import sys

_here = os.path.dirname(os.path.abspath("__file__")) if "__file__" not in dir() else os.path.dirname(__file__)
for cand in (_here, os.getcwd(), "/Workspace" + os.getcwd()):
    if cand and cand not in sys.path:
        sys.path.insert(0, cand)

# COMMAND ----------
import incremental_rule_job as job

# First run (no snapshot) SEEDS the rules snapshot and exits; run the full job
# first to populate tags, then this to seed. After that, change category_model.json
# and re-run to apply changes incrementally.
job.run()
