-- calibrate_confidence_threshold.sql
-- ---------------------------------------------------------------------------
-- Pick the ai_classify confidence cut (conf_threshold in ai_classify.py) that
-- best matches the rule engine (the POC's proxy ground truth), WITHOUT paying
-- for inference again: this sweeps thresholds over the full per-label
-- confidences already stored in ai_result_json from the last run.
--
-- Prereq: run ai_classify.py once with enable_confidence=true (the default) and
--         conf_threshold=0.0 (capture every label + its score). Then run this.
--
-- Edit these three to match your run, then run each query below:
--   AI tags table  : daria_krasavina.gm_voc.voc_classification_ai_classify_tags
--   Rule tags table: daria_krasavina.gm_voc.voc_classification_rule_tags
--   Day            : 2026-06-11  (the day ai_classify ran)
--
-- The label->category_id map below matches shared/category_model.json (4 leaf
-- targets). Update it if the taxonomy changes.
-- ===========================================================================


-- QUERY 1 — Over-tagging magnitude: labels per sentence at each threshold.
-- No rules needed. Watch avg_labels_per_sentence and multi_label fall as the
-- cut rises; this alone shows the over-tagging shrinking.
WITH params AS (SELECT explode(array(0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)) AS thr),
resp AS (
  SELECT id_verbatim, r.confidence_score AS conf
  FROM daria_krasavina.gm_voc.voc_classification_ai_classify_tags
  LATERAL VIEW OUTER explode(
    from_json(ai_result_json,
      'struct<response:array<struct<value:string,confidence_score:double,rationale:string>>,error_message:string>'
    ).response
  ) e AS r
  WHERE to_date(document_date) = '2026-06-11'
),
per_sentence AS (
  SELECT p.thr, s.id_verbatim,
         count(CASE WHEN s.conf >= p.thr THEN 1 END) AS n_labels
  FROM resp s CROSS JOIN params p
  GROUP BY p.thr, s.id_verbatim
)
SELECT thr,
       count(*)                                            AS sentences,
       round(avg(n_labels), 3)                             AS avg_labels_per_sentence,
       sum(CASE WHEN n_labels = 0 THEN 1 ELSE 0 END)       AS zero_label,
       sum(CASE WHEN n_labels = 1 THEN 1 ELSE 0 END)       AS one_label,
       sum(CASE WHEN n_labels > 1 THEN 1 ELSE 0 END)       AS multi_label
FROM per_sentence
GROUP BY thr
ORDER BY thr;


-- QUERY 2 — Precision / recall / F1 vs the rule engine, per leaf topic per
-- threshold. Over-tagging = high ai_only + low precision; pick the threshold
-- that lifts precision without collapsing recall (maximize F1, or hit your
-- precision target). Compared on the sentences BOTH tables share (inner join),
-- same as compare_approaches_job.py.
WITH params AS (SELECT explode(array(0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)) AS thr),
lab_map AS (
  SELECT * FROM VALUES
    ('CC Advisor - Confusing/Makes No Sense', 'cc_advisor_confusing_makes_no_sense'),
    ('CC Advisor - Inaccurate Information',   'cc_advisor_inaccurate_information'),
    ('Loyalty Rewards - Points',              'loyalty_rewards_points'),
    ('Points - Redeem',                       'points_redeem')
  AS t(label, cid)
),
-- sentences present in BOTH tables for the day (the comparison universe)
shared AS (
  SELECT a.id_verbatim FROM
    (SELECT DISTINCT id_verbatim FROM daria_krasavina.gm_voc.voc_classification_ai_classify_tags
       WHERE to_date(document_date) = '2026-06-11') a
  JOIN
    (SELECT DISTINCT id_verbatim FROM daria_krasavina.gm_voc.voc_classification_rule_tags
       WHERE to_date(document_date) = '2026-06-11') r
  ON a.id_verbatim = r.id_verbatim
),
-- AI positives per (threshold, sentence, leaf) from the stored full response
ai_pos AS (
  SELECT DISTINCT p.thr, a.id_verbatim, m.cid
  FROM daria_krasavina.gm_voc.voc_classification_ai_classify_tags a
  LATERAL VIEW explode(
    from_json(a.ai_result_json,
      'struct<response:array<struct<value:string,confidence_score:double,rationale:string>>,error_message:string>'
    ).response
  ) e AS r
  JOIN lab_map m ON r.value = m.label
  CROSS JOIN params p
  WHERE to_date(a.document_date) = '2026-06-11' AND r.confidence_score >= p.thr
),
-- rule positives per (sentence, leaf): unpivot the four 0/1 columns
rule_pos AS (
  SELECT id_verbatim, cid FROM (
    SELECT id_verbatim, stack(4,
      'cc_advisor_confusing_makes_no_sense', cc_advisor_confusing_makes_no_sense,
      'cc_advisor_inaccurate_information',   cc_advisor_inaccurate_information,
      'loyalty_rewards_points',              loyalty_rewards_points,
      'points_redeem',                       points_redeem) AS (cid, v)
    FROM daria_krasavina.gm_voc.voc_classification_rule_tags
    WHERE to_date(document_date) = '2026-06-11'
  ) WHERE v = 1
),
grid AS (  -- every (threshold, shared sentence, leaf)
  SELECT p.thr, s.id_verbatim, m.cid
  FROM params p CROSS JOIN shared s CROSS JOIN lab_map m
),
flags AS (
  SELECT g.thr, g.cid,
         CASE WHEN ap.id_verbatim IS NOT NULL THEN 1 ELSE 0 END AS ai_pos,
         CASE WHEN rp.id_verbatim IS NOT NULL THEN 1 ELSE 0 END AS rule_pos
  FROM grid g
  LEFT JOIN ai_pos   ap ON ap.thr = g.thr AND ap.cid = g.cid AND ap.id_verbatim = g.id_verbatim
  LEFT JOIN rule_pos rp ON rp.cid = g.cid AND rp.id_verbatim = g.id_verbatim
)
SELECT cid AS topic, thr,
       sum(rule_pos)                                          AS rule_pos,
       sum(ai_pos)                                            AS ai_pos,
       sum(ai_pos * rule_pos)                                 AS agree,
       sum(CASE WHEN ai_pos = 1 AND rule_pos = 0 THEN 1 ELSE 0 END) AS ai_only_overtag,
       sum(CASE WHEN ai_pos = 0 AND rule_pos = 1 THEN 1 ELSE 0 END) AS rule_only_miss,
       round(sum(ai_pos * rule_pos) / nullif(sum(ai_pos), 0), 3)    AS precision_vs_rules,
       round(sum(ai_pos * rule_pos) / nullif(sum(rule_pos), 0), 3)  AS recall_vs_rules,
       round(2 * sum(ai_pos * rule_pos) /
             nullif(sum(ai_pos) + sum(rule_pos), 0), 3)             AS f1_vs_rules
FROM flags
GROUP BY cid, thr
ORDER BY cid, thr;
