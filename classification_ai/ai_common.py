"""
ai_common.py
------------
Shared, dependency-free helpers for the AI classification tracks
(ai_query_sql.py, ai_classify.py, ab_test_prompt.py):

  * find_category_model() / load_categories()  - locate + parse the SAME
    shared/category_model.json the rule engine uses, so every classifier works
    against identical categories.
  * build_prompt()                             - the multi-label ai_query prompt.

No pyspark import here, so it loads cleanly in DBSQL-batch jobs and locally.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def find_category_model(filename=None):
    """Locate a category-model JSON regardless of folder layout (repo vs. a flat
    cluster upload where the modules + json are co-located).

    `filename` may be None/"" (the default category_model.json / v1), a basename
    (e.g. category_model_v2.json, resolved against the known dirs so the bundle's
    shared/ copy is found), or an existing path (used as-is).
    """
    filename = (filename or "").strip()
    if filename and os.path.exists(filename):
        return filename
    base = os.path.basename(filename) if filename else "category_model.json"
    repo = os.path.dirname(HERE)
    for c in (os.path.join(HERE, base),
              os.path.join(repo, "shared", base),
              os.path.join(HERE, "shared", base),
              os.path.join(os.getcwd(), base),
              os.path.join(os.getcwd(), "shared", base)):
        if os.path.exists(c):
            return c
    raise FileNotFoundError("%s not found near %s" % (base, HERE))


def load_categories(rules_path=None):
    """Return category metadata from category_model.json.

    Returns:
      target_labels : {category_name: short description}  (leaf comparison targets)
      name_to_id    : {category_name: category_id}         (all categories)
      all_ids       : [category_id ...] in model order      (leaves + parents)
      ancestors     : {category_id: [ancestor_id ...]}      (nearest parent first)
      meta          : {category_id: {name, path, is_target}}
    """
    rules_path = find_category_model(rules_path)
    with open(rules_path) as f:
        rules = json.load(f)

    name_to_id = {n["name"]: n["id"] for n in rules["nodes"]}
    target_labels = {}
    for node in rules["nodes"]:
        if node.get("comparison_target"):
            # Prefer `ai_description` (the classifier-facing definition with full
            # include/exclude) when present; fall back to the customer-authored
            # `description`. This keeps the customer's `description` untouched while
            # letting the model files be the single source of truth for the AI
            # tracks. 1000 = the ai_classify v2.1 label-description cap.
            desc = " ".join((node.get("ai_description")
                             or node.get("description") or node["name"]).split())
            target_labels[node["name"]] = desc[:1000]
    ancestors = {}
    for n in rules["nodes"]:
        ancestors[n["id"]] = [name_to_id[a] for a in reversed(n.get("path", []))
                              if a in name_to_id]
    meta = {n["id"]: {"name": n["name"], "path": n.get("path", []),
                      "is_target": n.get("comparison_target", False)}
            for n in rules["nodes"]}
    all_ids = [n["id"] for n in rules["nodes"]]
    return target_labels, name_to_id, all_ids, ancestors, meta


def build_prompt(target_labels):
    """Multi-label classification prompt for ai_query: return ALL applicable
    categories as a JSON array (empty if none), with strict guidance against
    tagging filler. Tightened to fix heavy over-tagging observed on real data."""
    lines = [
        "You label a customer's sentence from a call-center transcript.",
        "Assign a category ONLY when the sentence CLEARLY and EXPLICITLY expresses "
        "it. MOST sentences match nothing — return [] for greetings, small talk, "
        "back-channel, acknowledgments, and generic or short clarifying questions "
        "(e.g. 'What?', 'Huh?', 'Pardon me?', 'Get what?', 'I don't know.', "
        "'Okay.', 'Um.'). Those are NOT categories.",
        "Each category's definition below states exactly when to assign it and what "
        "to exclude — follow those definitions precisely.",
        "If surrounding conversation is shown for context, classify ONLY the "
        "sentence marked between >>> and <<<; if no markers are present, classify "
        "the whole text.",
        "Categories and their definitions:",
    ]
    for name, desc in target_labels.items():
        lines.append("- %s: %s" % (name, desc))
    lines.append("Reply with ONLY a JSON array of the exact category names that "
                 "clearly apply, e.g. [\"Loyalty Rewards - Points\"]. If none "
                 "apply, reply []. Text: ")
    return " ".join(lines)
