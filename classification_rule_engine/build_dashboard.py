"""
build_dashboard.py
------------------
Renders a self-contained HTML review dashboard from the outputs produced by
run_local.py (run_summary.json + representative_verbatims.json). No external
JS/CSS dependencies so it opens straight in a browser.

    python3 classification_rule_engine/build_dashboard.py --out outputs/
"""
import argparse
import html
import json
import os

CHICKLET = ('<span style="display:inline-block;background:#1f6feb;color:#fff;'
            'border-radius:10px;padding:1px 8px;margin:1px 2px;font-size:11px">'
            '{t}</span>')


def esc(s):
    return html.escape(str(s if s is not None else ""))


def render(out_dir):
    with open(os.path.join(out_dir, "run_summary.json")) as f:
        summary = json.load(f)
    with open(os.path.join(out_dir, "representative_verbatims.json")) as f:
        examples = json.load(f)

    freq = summary["topic_frequencies"]
    maxc = max([r["sentences_tagged"] for r in freq] + [1])

    rows = []
    for r in freq:
        star = " ★" if r["is_comparison_target"] else ""
        barw = int(100.0 * r["sentences_tagged"] / maxc)
        rows.append(
            "<tr><td>{name}{star}<br><small style='color:#888'>{path}</small></td>"
            "<td style='text-align:right'>{sent}</td>"
            "<td style='text-align:right'>{doc}</td>"
            "<td style='text-align:right'>{pct}%</td>"
            "<td style='width:220px'><div style='background:#e6edf3;border-radius:4px'>"
            "<div style='background:#1f6feb;height:14px;width:{barw}%;border-radius:4px'></div>"
            "</div></td></tr>".format(
                name=esc(r["topic"]), star=star, path=esc(r["path"]),
                sent=r["sentences_tagged"], doc=r["documents_tagged"],
                pct=r["pct_of_in_scope_sentences"], barw=barw))

    ex_blocks = []
    for topic, items in examples.items():
        if not items:
            continue
        lis = []
        for it in items:
            chick = "".join(CHICKLET.format(t=esc(t)) for t in it["terms"])
            lis.append("<li>{chick}<br><span>{text}</span> "
                       "<small style='color:#999'>({nid})</small></li>".format(
                           chick=chick, text=esc(it["text"]),
                           nid=esc(it["natural_id"])))
        ex_blocks.append(
            "<details open><summary style='font-weight:600;font-size:15px'>{t} "
            "<small style='color:#888'>({n} examples)</small></summary>"
            "<ul style='line-height:1.7'>{lis}</ul></details>".format(
                t=esc(topic), n=len(items), lis="".join(lis)))

    cooc = "".join("<li>{k}: <b>{v}</b></li>".format(k=esc(k), v=v)
                   for k, v in summary.get("cooccurrences", {}).items()) or "<li>none</li>"

    doc = """<!doctype html><html><head><meta charset="utf-8">
<title>GM VOC POC — Topic Model Dashboard</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:24px;color:#1a1a1a}}
 h1{{font-size:22px}} h2{{font-size:16px;margin-top:28px;border-bottom:1px solid #eee;padding-bottom:4px}}
 table{{border-collapse:collapse;width:100%;font-size:14px}}
 th,td{{padding:6px 10px;border-bottom:1px solid #eee;vertical-align:top}}
 th{{text-align:left;color:#555;background:#fafafa}}
 .cards{{display:flex;gap:14px;flex-wrap:wrap;margin:12px 0}}
 .card{{background:#f6f8fa;border:1px solid #e6edf3;border-radius:8px;padding:12px 16px;min-width:150px}}
 .card b{{font-size:22px;display:block}} details{{margin:10px 0}} ul{{margin:6px 0}}
 small{{font-size:11px}}
</style></head><body>
<h1>GM &times; Databricks — VOC Topic Model (POC)</h1>
<p style="color:#666">Replication of Qualtrics XM Discover topic tagging &middot; run {ts}
&middot; rules from <code>{src}</code> &middot; join on <code>{jk}</code></p>

<div class="cards">
 <div class="card"><small>Sentences processed</small><b>{n_total:,}</b></div>
 <div class="card"><small>In scope (EN / audio / customer)</small><b>{n_scope:,}</b></div>
 <div class="card"><small>Out of scope</small><b>{n_out:,}</b></div>
 <div class="card"><small>Distinct documents tagged</small><b>{n_docs:,}</b></div>
 <div class="card"><small>Throughput</small><b>{tput}/s</b></div>
 <div class="card"><small>Elapsed</small><b>{elapsed}s</b></div>
</div>

<h2>Topic frequencies <small style="color:#888">(★ = POC comparison target)</small></h2>
<table><tr><th>Topic</th><th>Sentences</th><th>Documents</th><th>% in-scope</th><th>Volume</th></tr>
{rows}</table>

<h2>Multi-topic co-occurrences</h2><ul>{cooc}</ul>

<h2>Representative verbatims <small style="color:#888">(chicklets = matched rule terms)</small></h2>
{examples}

<hr><p style="color:#999;font-size:12px">Scope filter: {scope}. Tags stored per sentence as
topic columns + matched-term explanations in <code>tagged_sentences.csv</code>, mirroring
XM Discover attribute storage for direct validation against the control set.</p>
</body></html>""".format(
        ts=esc(summary["run_timestamp"]), src=esc(summary.get("rules_source")),
        jk=esc(summary.get("join_key")),
        n_total=summary["sentences_processed"], n_scope=summary["sentences_in_scope"],
        n_out=summary["sentences_out_of_scope"], n_docs=summary["distinct_documents"],
        tput=summary["throughput_sentences_per_sec"], elapsed=summary["elapsed_seconds"],
        rows="".join(rows), cooc=cooc, examples="".join(ex_blocks) or "<p>No tagged verbatims.</p>",
        scope=esc(summary.get("scope_filter")))

    path = os.path.join(out_dir, "dashboard.html")
    with open(path, "w") as f:
        f.write(doc)
    print("Wrote %s" % path)


def main():
    ap = argparse.ArgumentParser()
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--out", default=os.path.join(repo, "outputs"))
    args = ap.parse_args()
    render(args.out)


if __name__ == "__main__":
    main()
