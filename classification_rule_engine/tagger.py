"""
tagger.py
---------
Shared, dependency-free tagging core used by BOTH the local runner
(run_local.py) and the Databricks PySpark job (voc_topic_model_job.py).

Keeping this in one place guarantees the local validation and the production
Spark run apply byte-identical rule logic. No pyspark import here.

Public API:
    load_rules(path)                    -> dict (parsed category_model.json)
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


def find_category_model():
    """Locate shared/category_model.json regardless of folder layout.

    Files live in a track folder (classification_rule_engine/ or classification_ai/) while category_model.json
    lives in shared/. On Databricks the bundle co-locates modules + category_model.json.
    Search order covers both the local repo layout and a flat cluster upload.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(here)
    candidates = [
        os.path.join(here, "category_model.json"),               # flat / co-located (cluster)
        os.path.join(repo, "shared", "category_model.json"),     # repo layout, sibling folder
        os.path.join(here, "shared", "category_model.json"),     # shared under cwd
        os.path.join(os.getcwd(), "category_model.json"),
        os.path.join(os.getcwd(), "shared", "category_model.json"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    raise FileNotFoundError(
        "category_model.json not found; looked in: %s" % ", ".join(candidates))


def load_rules(path=None):
    if path is None:
        path = find_category_model()
    with open(path) as f:
        return json.load(f)


def build_ancestor_map(rules):
    """category_id -> [ancestor category_ids ...] (nearest parent first).

    Node paths in the model are stored as category *names*; map those to ids so
    a leaf match can roll up to its parents, e.g. Points-Redeem -> Points ->
    Loyalty - Rewards -> Loyalty, mirroring XM Discover's category hierarchy.
    """
    name_to_id = {n["name"]: n["id"] for n in rules["nodes"]}
    ancestors = {}
    for n in rules["nodes"]:
        chain = []
        for ancestor_name in reversed(n.get("path", [])):  # nearest parent first
            aid = name_to_id.get(ancestor_name)
            if aid:
                chain.append(aid)
        ancestors[n["id"]] = chain
    return ancestors


class Tagger(object):
    """Applies the global scope filter and assigns categories (with roll-up)."""

    def __init__(self, rules):
        self.rules = rules
        gf = rules.get("global_filter")
        self.global_node = CompiledNode({
            "id": "global_filter", "name": gf["name"], "lanes": gf["lanes"],
        }) if gf else None

        # Compile every category node. Only rule-bearing nodes are evaluated
        # directly; rule-free parents (e.g. Contact Center) are populated only
        # by roll-up from a matching descendant.
        all_nodes = compile_nodes(rules["nodes"])
        self._by_id = {n.id: n for n in all_nodes}
        self._evaluatable = [n for n in all_nodes if n.keywords is not None]
        self._ancestors = build_ancestor_map(rules)

        # Category output order: all categories, so parents get their own columns
        # and counts roll up like a real category model.
        self._all_ids = [n["id"] for n in rules["nodes"]]
        self._meta = {n["id"]: {"name": n["name"], "path": n.get("path", []),
                                "is_target": n.get("comparison_target", False)}
                      for n in rules["nodes"]}

    def in_scope(self, text, attrs):
        """Global 'DBX POC' filter: English, audio, customer-side, non-boilerplate."""
        if self.global_node is None:
            return True, []
        matched, terms = self.global_node.evaluate(Context(text, attrs))
        return matched, terms

    def tag(self, text, attrs):
        """Return {category_id: {terms, name, path, via}} for in-scope rows.

        Multi-label: a sentence may match several categories. Roll-up: when a
        category matches, its ancestor categories are also assigned (marked
        via='rollup') so parent counts include their children, as in XM Discover.
        Out-of-scope rows return {} (XM Discover only tags EN/audio/customer).
        """
        ctx = Context(text, attrs)
        in_scope, _ = self.in_scope(text, attrs)
        if not in_scope:
            return {}

        results = {}
        # 1. Direct matches from rule-bearing categories.
        for node in self._evaluatable:
            matched, terms = node.evaluate(ctx)
            if matched:
                results[node.id] = {"terms": terms, "name": node.name,
                                    "path": node.path, "via": "direct"}
        # 2. Roll up to ancestors of every directly matched category.
        for matched_id in list(results.keys()):
            for aid in self._ancestors.get(matched_id, []):
                if aid not in results:  # keep a direct match if the parent also matched
                    m = self._meta.get(aid, {})
                    results[aid] = {"terms": [], "name": m.get("name", aid),
                                    "path": m.get("path", []),
                                    "via": "rollup:%s" % matched_id}
        return results

    def topic_ids(self):
        # All categories (leaves + parents) so roll-up has columns to land in.
        return list(self._all_ids)

    def topic_meta(self):
        return {cid: dict(self._meta[cid]) for cid in self._all_ids}


def build_tagger(rules):
    return Tagger(rules)
