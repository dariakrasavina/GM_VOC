"""
tagger.py
---------
Shared, dependency-free tagging core used by BOTH the local runner
(run_local.py) and the Databricks PySpark job (voc_topic_model_job.py).

Keeping this in one place guarantees the local validation and the production
Spark run apply byte-identical rule logic. No pyspark import here.

Public API:
    load_rules(path)                    -> dict (parsed rules.json)
    build_tagger(rules)                 -> Tagger
    Tagger.in_scope(text, attrs)        -> (bool, list[reason_terms])
    Tagger.tag(text, attrs)             -> dict {topic_id: {matched, terms}}
    ATTR_FIELDS                         -> metadata/sentence fields the rules read
"""
import json
import os

from rule_engine import CompiledNode, Context, compile_nodes

# Attribute fields referenced by the rule set (sentence + joined metadata).
# The Spark job must supply these into the per-row attrs dict.
ATTR_FIELDS = [
    "id_source",        # sentence: 'audio'
    "verbatimtype",     # sentence: 'clientverbatim' / 'agentverbatim'
    "language",         # sentence: 'english' ...
    "call_direction",   # metadata: 'Inbound' / 'Outbound' ...
    "cc_lob_mv",        # metadata: line-of-business
]


def find_rules_json():
    """Locate shared/rules.json regardless of folder layout.

    Files live in a track folder (rule_engine/ or ai_solution/) while rules.json
    lives in shared/. On Databricks the bundle co-locates modules + rules.json.
    Search order covers both the local repo layout and a flat cluster upload.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(here)
    candidates = [
        os.path.join(here, "rules.json"),               # flat / co-located (cluster)
        os.path.join(repo, "shared", "rules.json"),     # repo layout, sibling folder
        os.path.join(here, "shared", "rules.json"),     # shared under cwd
        os.path.join(os.getcwd(), "rules.json"),
        os.path.join(os.getcwd(), "shared", "rules.json"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    raise FileNotFoundError(
        "rules.json not found; looked in: %s" % ", ".join(candidates))


def load_rules(path=None):
    if path is None:
        path = find_rules_json()
    with open(path) as f:
        return json.load(f)


class Tagger(object):
    """Applies the global scope filter and the comparison-target topics."""

    def __init__(self, rules):
        self.rules = rules
        gf = rules.get("global_filter")
        self.global_node = CompiledNode({
            "id": "global_filter", "name": gf["name"], "lanes": gf["lanes"],
        }) if gf else None

        # Only nodes that actually carry rules are evaluated. We tag the four
        # comparison-target leaves; parent nodes without rules are structural.
        self.topics = [n for n in compile_nodes(rules["nodes"])
                       if (n.keywords is not None)]

    def in_scope(self, text, attrs):
        """Global 'DBX POC' filter: English, audio, customer-side, non-boilerplate."""
        if self.global_node is None:
            return True, []
        matched, terms = self.global_node.evaluate(Context(text, attrs))
        return matched, terms

    def tag(self, text, attrs):
        """Return {topic_id: {"matched": bool, "terms": [...]}} for in-scope rows.

        Out-of-scope rows return an empty dict (nothing tagged), mirroring how
        XM Discover only tags customer-side English audio verbatims.
        """
        ctx = Context(text, attrs)
        in_scope, _ = self.in_scope(text, attrs)
        if not in_scope:
            return {}
        results = {}
        for node in self.topics:
            matched, terms = node.evaluate(ctx)
            if matched:
                results[node.id] = {"terms": terms, "name": node.name,
                                    "path": node.path}
        return results

    def topic_ids(self):
        return [n.id for n in self.topics]

    def topic_meta(self):
        return {n.id: {"name": n.name, "path": n.path, "is_target": n.is_target}
                for n in self.topics}


def build_tagger(rules):
    return Tagger(rules)
