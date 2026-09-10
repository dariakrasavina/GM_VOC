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


def find_category_model():
    """Locate shared/category_model.json regardless of folder layout (repo vs. a
    flat cluster upload where the modules + json are co-located)."""
    repo = os.path.dirname(HERE)
    for c in (os.path.join(HERE, "category_model.json"),
              os.path.join(repo, "shared", "category_model.json"),
              os.path.join(HERE, "shared", "category_model.json"),
              os.path.join(os.getcwd(), "category_model.json"),
              os.path.join(os.getcwd(), "shared", "category_model.json")):
        if os.path.exists(c):
            return c
    raise FileNotFoundError("category_model.json not found near %s" % HERE)


def load_categories(rules_path=None):
    """Return category metadata from category_model.json.

    Returns:
      target_labels : {category_name: short description}  (leaf comparison targets)
      name_to_id    : {category_name: category_id}         (all categories)
      all_ids       : [category_id ...] in model order      (leaves + parents)
      ancestors     : {category_id: [ancestor_id ...]}      (nearest parent first)
      meta          : {category_id: {name, path, is_target}}
    """
    if rules_path is None:
        rules_path = find_category_model()
    with open(rules_path) as f:
        rules = json.load(f)

    name_to_id = {n["name"]: n["id"] for n in rules["nodes"]}
    target_labels = {}
    for node in rules["nodes"]:
        if node.get("comparison_target"):
            desc = " ".join((node.get("description") or node["name"]).split())
            target_labels[node["name"]] = desc[:400]
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
        "Do NOT tag 'CC Advisor - Confusing/Makes No Sense' merely because the "
        "customer asks a question or sounds unsure — tag it ONLY when the customer "
        "explicitly says the advisor, information, or instructions were confusing "
        "or made no sense.",
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
