"""
rule_engine.py
--------------
A dependency-free implementation of the XM Discover Designer query language,
used to replicate GM's Qualtrics topic-tagging rules on Databricks.

WHY PURE PYTHON (no pyspark import):
  All matching logic lives here as plain functions. The Spark job
  (voc_topic_model_job.py) wraps `compile_node` / `TopicMatcher` inside a
  pandas_udf, so the *exact same* code runs at scale on Databricks and is
  unit-testable locally without a JVM. Do not import pyspark in this module.

SUPPORTED SYNTAX (per XM Discover "Rule Types" documentation):
  - Implicit OR       : `foo bar baz`  or comma-separated `foo, bar`
  - Boolean AND / OR / NOT (must be uppercase)
  - Parentheses grouping
  - Quoted exact phrase: "make no sense"
  - Prefix NOT lane    : NOT(_verbatimtype:agentverbatim)
  - Wildcards          : `room*` (0+ chars), `room?` (exactly 1 char)
  - Fuzzy single term  : `attention~`  (edit-distance variants)
  - Proximity phrase   : "hotel room"~2  (words within N moves)
  - Attribute search   : _id_source:audio , language:english ,
                         cc_lob_mv:*reward* , attr:"quoted value"

EVALUATION MODEL:
  A node combines up to four "swim lanes": keywords (OR seed), and, and2
  (additional required conditions), not (exclusion). A sentence matches a node
  when:  keywords AND and AND and2 AND (NOT not).
  Empty lanes are treated as: keywords empty -> no match unless "*";
  and/and2 empty -> satisfied; not empty -> nothing excluded.

The matcher works over a token stream so proximity/position semantics are
faithful. Attribute predicates are resolved against a per-row `attrs` dict
supplied by the caller (sentence + joined metadata fields).
"""
import re

# ---------------------------------------------------------------------------
# Tokenization of the *sentence text* being searched.
# ---------------------------------------------------------------------------
_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?", re.I)


def tokenize_text(text):
    """Lowercased word tokens from a verbatim, preserving order for proximity."""
    if not text:
        return []
    return _WORD_RE.findall(text.lower())


# ---------------------------------------------------------------------------
# AST node types for a compiled query.
# ---------------------------------------------------------------------------
class Node(object):
    __slots__ = ()

    def match(self, ctx):  # pragma: no cover - interface
        raise NotImplementedError

    def terms(self):
        """Yield human-readable leaf terms (for explainability/chicklets)."""
        return []


class Or(Node):
    __slots__ = ("children",)

    def __init__(self, children):
        self.children = children

    def match(self, ctx):
        for c in self.children:
            m = c.match(ctx)
            if m is not None:
                return m
        return None

    def terms(self):
        for c in self.children:
            for t in c.terms():
                yield t


class And(Node):
    __slots__ = ("children",)

    def __init__(self, children):
        self.children = children

    def match(self, ctx):
        hits = []
        for c in self.children:
            m = c.match(ctx)
            if m is None:
                return None
            hits.extend(m)
        return hits

    def terms(self):
        for c in self.children:
            for t in c.terms():
                yield t


class Not(Node):
    """AND NOT: passes only when `base` matches and `excl` does not.

    When used as a bare exclusion lane, `base` is TrueNode so it acts as a pure
    negation over the whole context.
    """
    __slots__ = ("base", "excl")

    def __init__(self, base, excl):
        self.base = base
        self.excl = excl

    def match(self, ctx):
        m = self.base.match(ctx)
        if m is None:
            return None
        if self.excl.match(ctx) is not None:
            return None
        return m

    def terms(self):
        for t in self.base.terms():
            yield t


class TrueNode(Node):
    __slots__ = ()

    def match(self, ctx):
        return []  # matches, contributes no chicklet term


class PhraseTerm(Node):
    """A word, phrase, wildcard, fuzzy, or proximity search term."""
    __slots__ = ("raw", "matcher", "label")

    def __init__(self, raw, matcher, label):
        self.raw = raw
        self.matcher = matcher
        self.label = label

    def match(self, ctx):
        if self.matcher(ctx):
            return [self.label]
        return None

    def terms(self):
        yield self.label


class AttrTerm(Node):
    """Structured attribute predicate: attr:value (value may be wildcarded)."""
    __slots__ = ("attr", "pattern", "raw", "regex")

    def __init__(self, attr, pattern, raw):
        self.attr = attr.lower().lstrip("_")
        self.pattern = pattern
        self.raw = raw
        # Attribute values match as: exact (case-insensitive) OR wildcard.
        pat = re.escape(pattern.lower()).replace(r"\*", ".*").replace(r"\?", ".")
        self.regex = re.compile("^" + pat + "$")

    def match(self, ctx):
        val = ctx.attr(self.attr)
        if val is None:
            return None
        return [self.raw] if self.regex.match(str(val).lower()) else None

    def terms(self):
        yield self.raw


# ---------------------------------------------------------------------------
# Matching context: the sentence tokens + a per-row attribute resolver.
# ---------------------------------------------------------------------------
class Context(object):
    __slots__ = ("tokens", "token_set", "text_lower", "_attrs")

    def __init__(self, text, attrs=None):
        self.tokens = tokenize_text(text)
        self.token_set = set(self.tokens)
        self.text_lower = (text or "").lower()
        self._attrs = attrs or {}

    def attr(self, name):
        return self._attrs.get(name.lower().lstrip("_"))


# ---------------------------------------------------------------------------
# Leaf matcher builders (word / phrase / wildcard / fuzzy / proximity).
# ---------------------------------------------------------------------------
def _levenshtein_le(a, b, max_dist):
    """True if edit distance(a,b) <= max_dist. Small strings; cheap DP."""
    if abs(len(a) - len(b)) > max_dist:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        best = i
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            cur.append(v)
            if v < best:
                best = v
        if best > max_dist:
            return False
        prev = cur
    return prev[-1] <= max_dist


def _fuzzy_dist(word):
    # XM Discover default fuzzy edit distance scales with word length.
    n = len(word)
    if n <= 2:
        return 0
    if n <= 5:
        return 1
    return 2


def _make_word_matcher(term):
    """Single token, possibly with wildcards (*,?) or trailing fuzzy (~)."""
    fuzzy = term.endswith("~")
    core = term[:-1] if fuzzy else term
    core_l = core.lower()

    # Bare "*" is the match-all seed (used by the global scope filter).
    if core_l == "*":
        return lambda ctx: True

    if "*" in core_l or "?" in core_l:
        pat = re.escape(core_l).replace(r"\*", "[a-z0-9]*").replace(r"\?", "[a-z0-9]")
        rx = re.compile("^" + pat + "$")
        return lambda ctx: any(rx.match(tok) for tok in ctx.token_set)

    if fuzzy:
        d = _fuzzy_dist(core_l)
        if d == 0:
            return lambda ctx: core_l in ctx.token_set
        return lambda ctx: any(_levenshtein_le(core_l, tok, d) for tok in ctx.token_set)

    return lambda ctx: core_l in ctx.token_set


def _make_phrase_matcher(phrase, proximity=None):
    """Quoted phrase. If proximity is set, words must be within N 'moves'."""
    words = tokenize_text(phrase)
    if not words:
        return lambda ctx: False

    if proximity is None:
        # Exact contiguous phrase (order preserved).
        if len(words) == 1:
            w = words[0]
            return lambda ctx: w in ctx.token_set
        n = len(words)

        def contig(ctx):
            toks = ctx.tokens
            for i in range(len(toks) - n + 1):
                if toks[i:i + n] == words:
                    return True
            return False
        return contig

    # Proximity: all words present within a window; position swaps allowed.
    # "moves" ~= max span between the first and last of the required words.
    target = set(words)
    n = len(words)
    span = n + proximity  # generous window consistent with move semantics

    def prox(ctx):
        toks = ctx.tokens
        positions = {w: [] for w in target}
        for idx, tok in enumerate(toks):
            if tok in positions:
                positions[tok].append(idx)
        if any(not positions[w] for w in target):
            return False
        # Slide a window; require every target word to appear within it.
        occ = sorted((idx, tok) for tok, idxs in positions.items() for idx in idxs)
        from collections import defaultdict
        left = 0
        count = defaultdict(int)
        distinct = 0
        for right in range(len(occ)):
            _, tr = occ[right]
            if count[tr] == 0:
                distinct += 1
            count[tr] += 1
            while occ[right][0] - occ[left][0] >= span:
                _, tl = occ[left]
                count[tl] -= 1
                if count[tl] == 0:
                    distinct -= 1
                left += 1
            if distinct == n:
                return True
        return False
    return prox


# ---------------------------------------------------------------------------
# Query parser: tokenizes a lane string, then recursive-descent into AST.
# Grammar (loosely, XM Discover flavored):
#   expr    := or_expr
#   or_expr := and_expr (('OR' | ',' | <implicit>) and_expr)*
#   and_expr:= not_expr ('AND' not_expr)*
#   not_expr:= atom ('NOT' atom)*        # infix NOT  => AND NOT
#            | 'NOT' '(' expr ')'        # prefix NOT
#   atom    := '(' expr ')' | phrase | proximity | attr | word
# ---------------------------------------------------------------------------
_TOKEN_RE = re.compile(r"""
    \s*(
        \(|\)|,                                   |  # structural
        "[^"]*"(?:~\d+)?                          |  # quoted phrase (+proximity)
        [^\s(),]+                                    # bare token
    )
""", re.X)


def _lex(s):
    return [m.group(1) for m in _TOKEN_RE.finditer(s) if m.group(1).strip() != ""]


class _Parser(object):
    def __init__(self, tokens):
        self.toks = tokens
        self.i = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def next(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def parse(self):
        if not self.toks:
            return TrueNode()
        node = self.parse_or()
        return node

    def parse_or(self):
        parts = [self.parse_and()]
        while True:
            t = self.peek()
            if t is None or t == ")":
                break
            if t == ",":
                self.next()
                if self.peek() in (None, ")"):
                    break
                parts.append(self.parse_and())
            elif t == "OR":
                self.next()
                parts.append(self.parse_and())
            elif t in ("AND", "NOT"):
                break  # handled by higher-precedence parsers above
            else:
                # implicit OR between adjacent terms
                parts.append(self.parse_and())
        return parts[0] if len(parts) == 1 else Or(parts)

    def parse_and(self):
        left = self.parse_not()
        while self.peek() == "AND":
            self.next()
            right = self.parse_not()
            if isinstance(left, And):
                left.children.append(right)
            else:
                left = And([left, right])
        return left

    def parse_not(self):
        # prefix NOT(...)
        if self.peek() == "NOT":
            # Could be prefix "NOT (" or infix; disambiguate by lookahead.
            save = self.i
            self.next()
            if self.peek() == "(":
                inner = self.parse_atom()  # consumes the (...)
                return Not(TrueNode(), inner)
            # Not followed by group: treat as infix with empty left -> rewind.
            self.i = save
        left = self.parse_atom()
        while self.peek() == "NOT":
            self.next()
            right = self.parse_atom()
            left = Not(left, right)
        return left

    def parse_atom(self):
        t = self.next()
        if t == "(":
            inner = self.parse_or()
            if self.peek() == ")":
                self.next()
            return inner
        return self._leaf(t)

    def _leaf(self, t):
        # Quoted phrase, optionally with proximity: "a b"~3
        m = re.match(r'^"([^"]*)"(?:~(\d+))?$', t)
        if m:
            phrase, prox = m.group(1), m.group(2)
            prox = int(prox) if prox is not None else None
            label = '"%s"%s' % (phrase, ("~%d" % prox) if prox is not None else "")
            return PhraseTerm(t, _make_phrase_matcher(phrase, prox), label)

        # Attribute predicate attr:value  (value may be quoted or wildcarded)
        if ":" in t and not t.startswith('"'):
            attr, _, val = t.partition(":")
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", attr):
                val = val.strip('"')
                return AttrTerm(attr, val, t)

        # Bare word / wildcard / fuzzy
        return PhraseTerm(t, _make_word_matcher(t), t)


def parse_lane(lane_text):
    """Compile one lane string into an AST Node. Empty -> None."""
    if not lane_text or not lane_text.strip():
        return None
    tokens = _lex(lane_text.strip())
    return _Parser(tokens).parse()


# ---------------------------------------------------------------------------
# A compiled topic node = combination of its four lanes.
# ---------------------------------------------------------------------------
class CompiledNode(object):
    __slots__ = ("id", "name", "path", "keywords", "and_", "and2", "not_",
                 "is_target")

    def __init__(self, node):
        self.id = node["id"]
        self.name = node["name"]
        self.path = node.get("path", [])
        self.is_target = node.get("comparison_target", False)
        lanes = node["lanes"]
        self.keywords = parse_lane(lanes.get("keywords", ""))
        self.and_ = parse_lane(lanes.get("and", ""))
        self.and2 = parse_lane(lanes.get("and2", ""))
        self.not_ = parse_lane(lanes.get("not", ""))

    def evaluate(self, ctx):
        """Return (matched: bool, matched_terms: list[str])."""
        chicklets = []

        # keywords lane (OR seed) is required when present.
        if self.keywords is not None:
            m = self.keywords.match(ctx)
            if m is None:
                return False, []
            chicklets.extend(m)
        # if no keywords lane at all, node has no positive seed -> no match
        else:
            return False, []

        for lane in (self.and_, self.and2):
            if lane is not None:
                m = lane.match(ctx)
                if m is None:
                    return False, []
                chicklets.extend(m)

        if self.not_ is not None and self.not_.match(ctx) is not None:
            return False, []

        # De-dup chicklets, drop empties (from TrueNode / attr-only lanes).
        seen = []
        for c in chicklets:
            if c and c not in seen:
                seen.append(c)
        return True, seen


def compile_nodes(nodes):
    return [CompiledNode(n) for n in nodes]
