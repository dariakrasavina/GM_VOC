"""
xlsx_to_category_model.py
-------------------------
Convert an XM Discover "Categorization Tree" .xlsx export (Category 1..7 +
Keywords / And / And(2) / Not columns) into the category_model.json shape the
rule engine + AI tracks consume.

Reuses the ids from an existing category_model.json (matched by category NAME) so
that the incremental jobs' diff sees a category as CHANGED (same id, different
lanes) rather than removed+added. New categories get a snake_case id.

Pure standard library (zipfile + ElementTree) — no pandas/openpyxl needed.

Usage:
  python xlsx_to_category_model.py \
      --xlsx "retagging/(C) APM Master ... v2.xlsx" \
      --base shared/category_model.json \
      --out  shared/category_model_v2.json
"""
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
# Column layout of the export (0-based).
C_DESC, C_KW, C_AND, C_AND2, C_NOT = 7, 9, 10, 11, 12
N_CAT_COLS = 7


def _read_sheet(xlsx):
    z = zipfile.ZipFile(xlsx)
    sst = ["".join(t.text or "" for t in si.iter(M + "t"))
           for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(M + "si")]

    def cidx(ref):
        letters = "".join(c for c in ref if c.isalpha())
        n = 0
        for ch in letters:
            n = n * 26 + (ord(ch) - 64)
        return n - 1

    sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in sheet.iter(M + "row"):
        cells, mx = {}, -1
        for c in row.findall(M + "c"):
            i = cidx(c.get("r")); mx = max(mx, i)
            t = c.get("t"); v = c.find(M + "v")
            if t == "s" and v is not None:
                cells[i] = sst[int(v.text)]
            elif t == "inlineStr":
                isv = c.find(M + "is")
                cells[i] = "".join(x.text or "" for x in isv.iter(M + "t")) if isv is not None else ""
            else:
                cells[i] = v.text if v is not None else ""
        rows.append([cells.get(i, "") for i in range(mx + 1)])
    return rows


def _slug(name):
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return re.sub(r"_+", "_", s)


def convert(xlsx, base_path, out_path, keep_base_global=False):
    rows = _read_sheet(xlsx)
    with open(base_path) as f:
        base = json.load(f)
    name_to_id = {n["name"]: n["id"] for n in base["nodes"]}
    targets = {n["name"] for n in base["nodes"] if n.get("comparison_target")}
    # Classifier-facing definitions live in ai_description (not in the xlsx), so
    # carry them over from the base model by name — a re-convert must not drop them.
    base_ai = {n["name"]: n["ai_description"]
               for n in base["nodes"] if n.get("ai_description")}

    def g(r, i):
        return (r[i] if i < len(r) else "") or ""

    def lanes(r):
        return {"keywords": g(r, C_KW), "and": g(r, C_AND),
                "and2": g(r, C_AND2), "not": g(r, C_NOT)}

    # Data rows = those with at least one Category column populated.
    data = [r for r in rows[2:] if any(g(r, k) for k in range(N_CAT_COLS))]

    global_filter, nodes, stack = None, [], []   # stack: (depth, name)
    for r in data:
        depth = next(k for k in range(N_CAT_COLS) if g(r, k))
        name = g(r, depth)
        stack[:] = [(d, n) for (d, n) in stack if d < depth]  # pop to parent
        if depth == 0:                                        # root -> global filter
            global_filter = {"name": name,
                             "description": base.get("global_filter", {}).get("description", ""),
                             "lanes": lanes(r)}
            stack.append((depth, name))
            continue
        path = [n for (d, n) in stack if d >= 1]               # exclude the root
        ln = lanes(r)
        node = {
            "id": name_to_id.get(name, _slug(name)),
            "name": name,
            "path": list(path),
            "level": depth + 1,
            "description": g(r, C_DESC),
        }
        if name in base_ai:                                     # carry classifier def
            node["ai_description"] = base_ai[name]
        node.update({
            "comparison_target": name in targets,
            "has_rules": any(ln.values()),
            "lanes": ln,
            "is_leaf": False,     # fixed up below
        })
        nodes.append(node)
        stack.append((depth, name))

    # is_leaf = no other node lists this one as its immediate parent.
    parents = {n["path"][-1] for n in nodes if n["path"]}
    base_names = set(name_to_id)
    for n in nodes:
        n["is_leaf"] = n["name"] not in parents
        # A category the customer ADDS (not in the base model) that is a rule-bearing
        # leaf defaults to comparison_target=true, so it is scored + tagged on BOTH
        # tracks. The rule engine tags any node that has rules regardless of this
        # flag; setting it true makes ai_classify (which only scores targets) and the
        # Qualtrics comparison include the new category too. Existing categories keep
        # the base model's target flag (matched by name above).
        if n["name"] not in base_names and n["is_leaf"] and n["has_rules"]:
            n["comparison_target"] = True

    # The root's And/And2 (POC audio/English scope) aren't part of the XM Discover
    # export; keep the base global filter so a rule change is tested on the same
    # population (and so the rule incremental job doesn't abort on a scope change).
    if keep_base_global:
        global_filter = base.get("global_filter", global_filter)
    model = {"source_workbook": xlsx, "global_filter": global_filter, "nodes": nodes}
    with open(out_path, "w") as f:
        json.dump(model, f, indent=2, ensure_ascii=False)
    return base, model


def _lanes_equal(a, b):
    keys = ("keywords", "and", "and2", "not")
    return {k: " ".join((a.get(k) or "").split()) for k in keys} == \
           {k: " ".join((b.get(k) or "").split()) for k in keys}


def diff(base, model):
    """Print what changed base -> model (per category + global filter)."""
    old = {n["id"]: n for n in base["nodes"]}
    new = {n["id"]: n for n in model["nodes"]}
    print("=" * 72)
    gf_changed = not _lanes_equal(base.get("global_filter", {}).get("lanes", {}),
                                  model.get("global_filter", {}).get("lanes", {}))
    print("GLOBAL scope filter changed: %s" % gf_changed)
    if gf_changed:
        ob = base.get("global_filter", {}).get("lanes", {})
        nb = model.get("global_filter", {}).get("lanes", {})
        for k in ("keywords", "and", "and2", "not"):
            if " ".join((ob.get(k) or "").split()) != " ".join((nb.get(k) or "").split()):
                print("   lane %-8s: len %d -> %d" % (k, len(ob.get(k) or ""), len(nb.get(k) or "")))
    print("-" * 72)
    for cid, n in new.items():
        tag = " [TARGET]" if n["comparison_target"] else ""
        if cid not in old:
            print("ADDED   %s%s" % (n["name"], tag))
            continue
        o = old[cid]
        lane_changed = [k for k in ("keywords", "and", "and2", "not")
                        if " ".join((o["lanes"].get(k) or "").split())
                        != " ".join((n["lanes"].get(k) or "").split())]
        desc_changed = " ".join((o.get("description") or "").split()) != \
                       " ".join((n.get("description") or "").split())
        if lane_changed or desc_changed:
            bits = []
            if lane_changed:
                bits.append("lanes: " + ",".join(lane_changed))
            if desc_changed:
                bits.append("DESCRIPTION")
            print("CHANGED %s%s -> %s" % (n["name"], tag, "; ".join(bits)))
        else:
            print("same    %s%s" % (n["name"], tag))
    for cid, o in old.items():
        if cid not in new:
            print("REMOVED %s" % o["name"])
    print("=" * 72)


if __name__ == "__main__":
    args = {}
    for a in sys.argv[1:]:
        if a.startswith("--") and "=" in a:
            k, v = a[2:].split("=", 1); args[k] = v
    base, model = convert(args["xlsx"], args["base"], args["out"],
                          keep_base_global=args.get("keep_base_global", "0") == "1")
    print("Wrote %s (%d nodes)" % (args["out"], len(model["nodes"])))
    diff(base, model)
