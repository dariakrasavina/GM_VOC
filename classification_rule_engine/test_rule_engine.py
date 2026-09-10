"""
Unit tests for the XM Discover query engine, checking each documented
syntax behavior. Run: python3 classification_rule_engine/test_rule_engine.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rule_engine import Context, parse_lane  # noqa: E402


def m(lane, text, attrs=None):
    node = parse_lane(lane)
    if node is None:
        return False
    return node.match(Context(text, attrs)) is not None


CASES = []


def check(desc, got, want):
    CASES.append((desc, got, want))


# --- Implicit OR ---
check("implicit OR hit", m("confused confuses confuse", "I am confused"), True)
check("implicit OR miss", m("confused confuses", "all good here"), False)
check("comma OR hit", m("false, inaccurate", "that is inaccurate"), True)

# --- Quoted exact phrase ---
check("phrase contiguous hit", m('"make no sense"', "this make no sense to me"), True)
check("phrase order matters", m('"make no sense"', "no sense make"), False)
check("phrase single word", m('"redeem"', "I want to redeem"), True)

# --- Boolean AND / NOT ---
check("AND both present", m("points AND redeem", "redeem my points"), True)
check("AND missing one", m("points AND redeem", "redeem my card"), False)
check("infix NOT excludes", m('confusion NOT "no confusion"', "no confusion here"), False)
check("infix NOT keeps", m('confusion NOT "no confusion"', "such confusion today"), True)
check("prefix NOT(attr) true when attr absent",
      m("NOT(_verbatimtype:agentverbatim)", "hello", {"verbatimtype": "clientverbatim"}), True)
check("prefix NOT(attr) false when attr present",
      m("NOT(_verbatimtype:agentverbatim)", "hello", {"verbatimtype": "agentverbatim"}), False)

# --- Wildcards ---
check("star wildcard 0+ hit room", m("room*", "the room was nice"), True)
check("star wildcard rooms", m("room*", "two rooms please"), True)
check("star bewilder*", m("bewilder*", "totally bewildered"), True)
check("question mark 1 char", m("room?", "roomy space"), True)
check("question mark not 0", m("room?", "the room"), False)

# --- Fuzzy ---
check("fuzzy typo attention", m("attention~", "atention please"), True)
check("fuzzy exact still hits", m("attention~", "attention please"), True)
check("fuzzy no match", m("attention~", "completely different"), False)

# --- Proximity ---
check("proximity within", m('"hotel room"~2', "the hotel and our room"), True)
check("proximity swapped", m('"hotel room"~3', "this 20 room hotel"), True)
check("proximity too far", m('"hotel room"~1', "hotel is far from the room over there"), False)

# --- Attribute search ---
check("attr exact", m("_id_source:audio", "x", {"id_source": "audio"}), True)
check("attr wrong", m("_id_source:audio", "x", {"id_source": "chat"}), False)
check("attr wildcard", m("cc_lob_mv:*reward*", "x", {"cc_lob_mv": "GM Rewards Team"}), True)
check("language attr", m("language:english", "x", {"language": "english"}), True)

# --- Parentheses grouping ---
check("group OR then AND",
      m("(expensive OR pricey) AND (cocktail OR drink)", "a pricey drink"), True)
check("group fails one side",
      m("(expensive OR pricey) AND (cocktail OR drink)", "a pricey meal"), False)

# --- Combined real rule fragment (Confusing/Makes No Sense keyword lane subset) ---
frag = '(confusion NOT ("no confusion")), confused, "makes no sense", nonsense'
check("real frag confused", m(frag, "I am so confused right now"), True)
check("real frag makes no sense", m(frag, "this makes no sense"), True)
check("real frag excluded", m(frag, "there is no confusion at all"), False)
check("real frag unrelated", m(frag, "the weather is nice"), False)


# --- Incremental tagging: diff, affected-column roll-up, and pre-filter ------
import incremental_rule_job as inc  # noqa: E402


def _rules(leaf_kw, parent_kw="", extra_nodes=None):
    """Tiny 2-level model: Parent -> Leaf, for planning tests."""
    nodes = [
        {"id": "parent", "name": "Parent", "path": [],
         "lanes": {"keywords": parent_kw, "and": "", "and2": "", "not": ""}},
        {"id": "leaf", "name": "Leaf", "path": ["Parent"], "comparison_target": True,
         "lanes": {"keywords": leaf_kw, "and": "", "and2": "", "not": ""}},
    ]
    nodes += (extra_nodes or [])
    return {"global_filter": {"name": "g",
            "lanes": {"keywords": "*", "and": "", "and2": "", "not": ""}},
            "nodes": nodes}


_old = _rules("redeem* use using used")
_new = _rules("redeem*")                       # the real Redeem fix

_p = inc.plan_incremental(_old, _new)
check("inc: leaf flagged changed", _p["changed"], ["leaf"])
check("inc: affected rolls up to parent", set(_p["affected_columns"]), {"leaf", "parent"})
check("inc: pre-filter bounds it", _p["needs_full_scan"], False)
check("inc: pre-filter has stem", "redeem" in _p["prefilter_substrings"], True)
check("inc: no-op when identical", inc.plan_incremental(_new, _new)["nothing_to_do"], True)

# global scope change must abort (can't be bounded incrementally)
_g = _rules("redeem*"); _g["global_filter"]["lanes"]["and"] = "_id_source:chat"
check("inc: global change flagged", inc.plan_incremental(_rules("redeem*"), _g)["global_changed"], True)

# added / removed categories
_added = _rules("redeem*", extra_nodes=[{"id": "leaf2", "name": "Leaf2",
    "path": ["Parent"], "lanes": {"keywords": "voucher*", "and": "", "and2": "", "not": ""}}])
_pa = inc.plan_incremental(_rules("redeem*"), _added)
check("inc: detects added category", _pa["added"], ["leaf2"])
_pr = inc.plan_incremental(_added, _rules("redeem*"))
check("inc: detects removed category", _pr["removed"], ["leaf2"])

# unbounded seeds -> full-scan fallback (fuzzy, then attribute)
check("inc: fuzzy seed -> full scan",
      inc.plan_incremental(_rules("redeem*"), _rules("redeeem~"))["needs_full_scan"], True)
check("inc: attr seed -> full scan",
      inc.plan_incremental(_rules("redeem*"), _rules("cc_lob_mv:*reward*"))["needs_full_scan"], True)

# term extraction specifics
_subs, _full = inc.extract_prefilter('"make no sense", confused, bewilder*')
check("inc: extract picks longest phrase word", "sense" in _subs, True)
check("inc: extract keeps wildcard stem", "bewilder" in _subs, True)
check("inc: extract not full for plain terms", _full, False)


def main():
    passed = sum(1 for _, g, w in CASES if g == w)
    for desc, got, want in CASES:
        flag = "ok  " if got == want else "FAIL"
        print("[%s] %s (got=%s want=%s)" % (flag, desc, got, want))
    print("\n%d/%d passed" % (passed, len(CASES)))
    sys.exit(0 if passed == len(CASES) else 1)


if __name__ == "__main__":
    main()
