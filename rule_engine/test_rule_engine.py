"""
Unit tests for the XM Discover query engine, checking each documented
syntax behavior. Run: python3 rule_engine/test_rule_engine.py
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


def main():
    passed = sum(1 for _, g, w in CASES if g == w)
    for desc, got, want in CASES:
        flag = "ok  " if got == want else "FAIL"
        print("[%s] %s (got=%s want=%s)" % (flag, desc, got, want))
    print("\n%d/%d passed" % (passed, len(CASES)))
    sys.exit(0 if passed == len(CASES) else 1)


if __name__ == "__main__":
    main()
