"""
make_demo_fixture.py
--------------------
The provided sample CSVs are synthetic OnStar roadside dialogue (only ~40
distinct sentences, none containing the four topics' vocabulary), so a real run
correctly produces zero tags. To demonstrate that the rule engine tags correctly
end-to-end, this builds a SCHEMA-IDENTICAL fixture with realistic customer
verbatims that exercise each node's rules (including negatives that must NOT tag).

Writes:
  test_data/demo_sentence_level.csv
  test_data/demo_metadata.csv

These share the exact columns/headers of the production tables, so run_local.py
(and the Spark job) process them with no changes.
"""
import csv
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TD = os.path.join(REPO, "test_data")

SENT_HEADER = ["id_document", "id_source", "id_verbatim", "language",
               "verbatimtype", "words", "cb_conv_id", "document_date",
               "natural_id", "sentence_id", "job_name", "cb_date", "dw_mod_ts"]
META_HEADER = ["case_id", "call_direction", "cc_lob_mv", "natural_id",
               "document_id", "language"]

# (words, verbatimtype, language, expected_topics)  -- expectations documented
# for the reader; the pipeline computes tags independently.
SENTENCES = [
    # Confusing / Makes No Sense  (positive)
    ("I am so confused by what the advisor told me", "clientverbatim", "english", ["confusing"]),
    ("honestly this makes no sense to me at all", "clientverbatim", "english", ["confusing"]),
    ("that explanation was totally unclear", "clientverbatim", "english", ["confusing"]),
    # Confusing (negatives that must NOT tag)
    ("no confusion here everything was clear", "clientverbatim", "english", []),
    ("I apologize for the confusion sir", "agentverbatim", "english", []),  # agent-side excluded

    # Inaccurate Information (positive)
    ("the advisor gave me incorrect information about my warranty", "clientverbatim", "english", ["inaccurate"]),
    ("I was told the wrong directions to the dealership", "clientverbatim", "english", []),  # dealer excluded
    ("they told me something that was just not true", "clientverbatim", "english", ["inaccurate"]),
    ("he gave me false information on my account status", "clientverbatim", "english", ["inaccurate"]),
    # Inaccurate (negative)
    ("what is wrong with my vehicle", "clientverbatim", "english", []),

    # Loyalty Rewards Points  (positive)
    ("how many rewards points do I have on my account", "clientverbatim", "english", ["points"]),
    ("I want to check my GM rewards points balance", "clientverbatim", "english", ["points"]),
    # Points negative
    ("that is a fair point about the appointment", "clientverbatim", "english", []),

    # Points - Redeem  (positive; needs redeem* AND point*/reward*)
    ("I would like to redeem my points for a service allowance", "clientverbatim", "english", ["points", "redeem"]),
    ("can I use my reward points to redeem an offer", "clientverbatim", "english", ["points", "redeem"]),
    # Redeem negative (redeem present but boilerplate / third-party)
    ("he wants to redeem his points", "clientverbatim", "english", []),

    # Out-of-scope rows (should be filtered before tagging)
    ("quiero canjear mis puntos de recompensa", "clientverbatim", "spanish", []),  # non-English
    ("Help is on the way.", "agentverbatim", "english", []),  # agent-side
]


def main():
    os.makedirs(TD, exist_ok=True)
    spath = os.path.join(TD, "demo_sentence_level.csv")
    mpath = os.path.join(TD, "demo_metadata.csv")

    with open(spath, "w", newline="") as fs, open(mpath, "w", newline="") as fm:
        sw = csv.writer(fs)
        mw = csv.writer(fm)
        sw.writerow(SENT_HEADER)
        mw.writerow(META_HEADER)
        for i, (words, vtype, lang, _exp) in enumerate(SENTENCES, 1):
            doc_id = 900000000000 + (i // 3)  # a few sentences share a document
            nat = "Audio;demo%d" % doc_id
            sw.writerow([doc_id, "audio", 1900000000 + i, lang, vtype, words,
                         1000 + i, "2025-09-15T10:00:00.000Z", nat,
                         100000 + i, "", "", ""])
            mw.writerow([doc_id, "Inbound", "GM Rewards", nat, doc_id, lang])

    print("Wrote %s (%d sentences)" % (spath, len(SENTENCES)))
    print("Wrote %s" % mpath)


if __name__ == "__main__":
    main()
