"""
run_local.py
------------
Local, stdlib-only driver that exercises the SAME tagging core as the Spark job
against the sample CSVs in test_data/. Use it to validate rule fidelity and to
produce the POC review outputs without a Databricks cluster.

    python3 classification_rule_engine/run_local.py \
        --sentences test_data/qualtrics_audio_transcripts_sentence_level_sample_data.csv \
        --metadata  test_data/qualtrics_audio_transcripts_metadata_sample_data.csv \
        --out outputs/

Join: sentence.natural_id == metadata.natural_id  (verified 1:1 on the sample;
id_document == case_id == document_id is an equivalent key).
"""
import argparse
import csv
import json
import os
import sys
import time
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tagger import ATTR_FIELDS, build_tagger, load_rules  # noqa: E402

csv.field_size_limit(10 * 1024 * 1024)

JOIN_KEY = "natural_id"
TEXT_FIELD = "words"
# Metadata columns pulled into the per-sentence attribute context for rules.
META_ATTRS = ["call_direction", "cc_lob_mv"]
# Sentence columns used for scope filtering / attributes.
SENT_ATTRS = ["id_source", "verbatimtype", "language"]
# Ordering fields (Andrew's multi-field sequence; start_time proxy via sentence_id).
ORDER_FIELDS = ["document_date", "sentence_id", "id_verbatim"]


def load_metadata(path):
    """natural_id -> {attr: value} for the fields our rules reference."""
    meta = {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = row.get(JOIN_KEY)
            if key is None:
                continue
            meta[key] = {a: row.get(a) for a in META_ATTRS}
    return meta


def iter_sentences(path):
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield row


def build_attrs(srow, meta):
    """Assemble the attribute dict the rule engine resolves attr:value against."""
    attrs = {}
    for a in SENT_ATTRS:
        attrs[a] = srow.get(a)
    m = meta.get(srow.get(JOIN_KEY), {})
    for a in META_ATTRS:
        attrs[a] = m.get(a)
    return attrs


def main():
    ap = argparse.ArgumentParser()
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--sentences", default=os.path.join(
        repo, "test_data", "qualtrics_audio_transcripts_sentence_level_sample_data.csv"))
    ap.add_argument("--metadata", default=os.path.join(
        repo, "test_data", "qualtrics_audio_transcripts_metadata_sample_data.csv"))
    ap.add_argument("--rules", default=None,
                    help="Path to category_model.json (default: auto-resolve shared/category_model.json)")
    ap.add_argument("--out", default=os.path.join(repo, "outputs"))
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    rules = load_rules(args.rules)
    tagger = build_tagger(rules)
    meta_map = tagger.topic_meta()
    topic_order = tagger.topic_ids()

    t0 = time.time()
    print("Loading metadata ...")
    meta = load_metadata(args.metadata)
    print("  %d metadata rows" % len(meta))

    tagged_path = os.path.join(args.out, "tagged_sentences.csv")
    out_cols = (["natural_id", "id_document", "id_verbatim", "document_date",
                 "language", "verbatimtype", "id_source", "words", "in_scope"]
                + [tid for tid in topic_order]
                + [tid + "__terms" for tid in topic_order]
                + ["topics_matched"])

    n_total = 0
    n_scope = 0
    sent_topic_counts = Counter()          # sentences per topic
    doc_topic = defaultdict(set)           # topic -> set(documents)
    examples = defaultdict(list)           # topic -> [(terms, text, natural_id)]
    cooccur = Counter()

    with open(tagged_path, "w", newline="") as fout:
        writer = csv.writer(fout)
        writer.writerow(out_cols)
        for srow in iter_sentences(args.sentences):
            n_total += 1
            text = srow.get(TEXT_FIELD, "") or ""
            attrs = build_attrs(srow, meta)
            in_scope, _ = tagger.in_scope(text, attrs)
            # Only in-scope rows (customer-side English audio, non-boilerplate)
            # are written, matching the POC requirement and the Spark job — so
            # the output covers the same population as the AI table.
            if not in_scope:
                continue
            n_scope += 1
            tags = tagger.tag(text, attrs)

            row_out = [
                srow.get("natural_id"), srow.get("id_document"),
                srow.get("id_verbatim"), srow.get("document_date"),
                srow.get("language"), srow.get("verbatimtype"),
                srow.get("id_source"), text, int(in_scope),
            ]
            matched_ids = []
            for tid in topic_order:
                hit = tid in tags
                row_out.append(int(hit))
                if hit:
                    matched_ids.append(tid)
            for tid in topic_order:
                row_out.append("; ".join(tags[tid]["terms"]) if tid in tags else "")
            row_out.append("|".join(matched_ids))
            writer.writerow(row_out)

            doc = srow.get("id_document")
            for tid in matched_ids:
                sent_topic_counts[tid] += 1
                doc_topic[tid].add(doc)
                if len(examples[tid]) < 15:
                    examples[tid].append({
                        "natural_id": srow.get("natural_id"),
                        "terms": tags[tid]["terms"],
                        "text": text,
                    })
            if len(matched_ids) > 1:
                cooccur["+".join(sorted(matched_ids))] += 1

    elapsed = time.time() - t0

    # ---- frequency + summary artifacts ----
    freq = []
    for tid in topic_order:
        freq.append({
            "topic_id": tid,
            "topic": meta_map[tid]["name"],
            "path": " > ".join(meta_map[tid]["path"]),
            "is_comparison_target": meta_map[tid]["is_target"],
            "sentences_tagged": sent_topic_counts[tid],
            "documents_tagged": len(doc_topic[tid]),
            "pct_of_in_scope_sentences": round(
                100.0 * sent_topic_counts[tid] / n_scope, 3) if n_scope else 0.0,
        })

    with open(os.path.join(args.out, "topic_frequencies.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(freq[0].keys()))
        w.writeheader()
        w.writerows(freq)

    with open(os.path.join(args.out, "representative_verbatims.json"), "w") as f:
        json.dump({meta_map[t]["name"]: examples[t] for t in topic_order}, f, indent=2)

    summary = {
        "run_timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_seconds": round(elapsed, 3),
        "sentences_processed": n_total,
        "sentences_in_scope": n_scope,
        "sentences_out_of_scope": n_total - n_scope,
        "throughput_sentences_per_sec": round(n_total / elapsed, 1) if elapsed else None,
        "distinct_documents": len({d for s in doc_topic.values() for d in s}),
        "topic_frequencies": freq,
        "cooccurrences": dict(cooccur),
        "rules_source": rules.get("source_workbook"),
        "join_key": JOIN_KEY,
        "scope_filter": rules.get("global_filter", {}).get("name"),
    }
    with open(os.path.join(args.out, "run_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    # ---- console report ----
    print("\n=== VOC Topic Model — local run ===")
    print("Sentences processed : %d" % n_total)
    print("In scope (EN/audio/customer): %d (%.1f%%)"
          % (n_scope, 100.0 * n_scope / n_total if n_total else 0))
    print("Elapsed: %.2fs  (%.0f sentences/sec)"
          % (elapsed, n_total / elapsed if elapsed else 0))
    print("\nTopic                                     Sentences  Documents  %%in-scope")
    for r in freq:
        star = "*" if r["is_comparison_target"] else " "
        print("%s %-40s %9d %10d %9.2f"
              % (star, r["topic"][:40], r["sentences_tagged"],
                 r["documents_tagged"], r["pct_of_in_scope_sentences"]))
    if cooccur:
        print("\nCo-occurrences (multi-topic sentences):")
        for k, v in cooccur.most_common():
            print("  %s : %d" % (k, v))
    print("\nOutputs written to: %s" % args.out)


if __name__ == "__main__":
    main()
