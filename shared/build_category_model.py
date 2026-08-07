"""
build_category_model.py
----------------------
Generates `category_model.json` from the source XM Discover export
(`requirements/Qualtrics_Parent_and_Leaf_Nodes.xlsx`).

We generate the config from the workbook instead of hand-transcribing the rule
strings, so the encoded rules are provably identical to what GM's text-analytics
team maintains in XM Discover Designer. Re-run this whenever the workbook changes.

Output schema (category_model.json):
{
  "source_workbook": "...",
  "global_filter": { "name", "description", "lanes": {keywords, and, and2, not} },
  "nodes": [
     { "id", "name", "path": [...ancestors...], "level", "description",
       "comparison_target": bool, "lanes": {keywords, and, and2, not} },
     ...
  ]
}

Pure stdlib (an .xlsx is a zip of XML), so it runs anywhere.
"""
import html
import json
import os
import re
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
XLSX = os.path.join(REPO, "requirements", "Qualtrics_Parent_and_Leaf_Nodes.xlsx")
OUT = os.path.join(HERE, "category_model.json")

# Column header -> internal key. Only the four active rule lanes are consumed by
# the POC engine; the Verbatim / Parent Doc / Other Verbatim lanes are unused in
# these four nodes and are carried through untouched for completeness.
COLMAP = {
    0: "cat1", 1: "cat2", 2: "cat3", 3: "cat4", 4: "cat5", 5: "cat6", 6: "cat7",
    7: "description", 8: "smart_other",
    9: "keywords", 10: "and", 11: "and2", 12: "not",
}
CAT_KEYS = ["cat1", "cat2", "cat3", "cat4", "cat5", "cat6", "cat7"]

# The four leaf nodes selected for the POC comparison against the XM Discover
# control set (chosen by GM for having meaningful, varied volume).
COMPARISON_TARGETS = {
    "CC Advisor - Confusing/Makes No Sense",
    "CC Advisor - Inaccurate Information",
    "Loyalty Rewards - Points",
    "Points - Redeem",
}


def _col_to_idx(letters):
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - 64)
    return idx - 1


def _shared_strings(z):
    raw = z.read("xl/sharedStrings.xml").decode("utf-8")
    out = []
    for si in re.findall(r"<si>(.*?)</si>", raw, re.S):
        text = "".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S))
        out.append(html.unescape(text))
    return out


def _first_sheet_path(z):
    # sheet1.xml holds the topic model; the others are Excel lambda metadata.
    return "xl/worksheets/sheet1.xml"


def parse_workbook():
    with zipfile.ZipFile(XLSX) as z:
        strings = _shared_strings(z)
        sheet = z.read(_first_sheet_path(z)).decode("utf-8")

    rows = re.findall(r'<row[^>]*r="(\d+)"[^>]*>(.*?)</row>', sheet, re.S)
    parsed_rows = []
    for rnum, body in rows:
        cells = re.findall(
            r'<c r="([A-Z]+)\d+"(?:[^>]*t="(\w+)")?[^>]*>(?:<v>(.*?)</v>)?', body
        )
        record = {}
        for col, typ, val in cells:
            if typ == "s" and val != "":
                key = COLMAP.get(_col_to_idx(col))
                if key:
                    record[key] = strings[int(val)].strip()
        parsed_rows.append((int(rnum), record))
    return parsed_rows


def build_config():
    parsed_rows = parse_workbook()

    global_filter = None
    nodes = []
    # Running ancestor names keyed by hierarchy level (1-based).
    level_names = {}

    for rnum, rec in parsed_rows:
        # Determine this row's level from which Category column is populated.
        level = None
        name = None
        for lvl, key in enumerate(CAT_KEYS, start=1):
            if rec.get(key):
                level = lvl
                name = rec[key]
                break
        if level is None:
            continue  # blank row

        # Skip the workbook header row (literal column titles "Category 1"...).
        if name in {"Category 1", "Category 2", "Category 3"}:
            continue

        lanes = {
            "keywords": rec.get("keywords", ""),
            "and": rec.get("and", ""),
            "and2": rec.get("and2", ""),
            "not": rec.get("not", ""),
        }
        description = rec.get("description", "")

        # The Cat1 "DBX POC" row is the global in-scope filter, not a topic.
        if level == 1 and name.upper().startswith("DBX POC"):
            global_filter = {
                "name": name,
                "description": description,
                "lanes": lanes,
            }
            continue

        # Reset deeper levels, then record this node's name at its level.
        for deeper in range(level, 8):
            level_names.pop(deeper, None)
        path = [level_names[l] for l in sorted(level_names) if l < level]
        level_names[level] = name

        nodes.append({
            "id": re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_"),
            "name": name,
            "path": path,
            "level": level,
            "description": description,
            "comparison_target": name in COMPARISON_TARGETS,
            "has_rules": any(v.strip() for v in lanes.values()),
            "lanes": lanes,
        })

    # Mark leaves (no other node lists this node in its path).
    all_paths = [tuple(n["path"]) for n in nodes]
    for n in nodes:
        node_path = tuple(n["path"] + [n["name"]])
        n["is_leaf"] = not any(p[: len(node_path)] == node_path for p in all_paths)

    config = {
        "source_workbook": os.path.relpath(XLSX, REPO),
        "global_filter": global_filter,
        "nodes": nodes,
    }
    return config


def main():
    config = build_config()
    with open(OUT, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    print("Wrote %s" % OUT)
    print("Global filter: %s" % (config["global_filter"] or {}).get("name"))
    print("Nodes: %d (comparison targets: %d)" % (
        len(config["nodes"]),
        sum(1 for n in config["nodes"] if n["comparison_target"]),
    ))
    for n in config["nodes"]:
        flag = " *TARGET*" if n["comparison_target"] else ""
        print("  L%d %s%s | path=%s | rules=%s" % (
            n["level"], n["name"], flag, " > ".join(n["path"]) or "(root)",
            n["has_rules"]))


if __name__ == "__main__":
    main()
