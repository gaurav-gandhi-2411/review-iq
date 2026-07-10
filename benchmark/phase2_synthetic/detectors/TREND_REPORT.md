# Phase 2 Trend Detector -- Flagged Products

> SYNTHETIC-VALIDATED DETECTOR -- see `trend.py` module docstring. NOT proven against real
> seller data.
>
> **MOST REAL-DATA-DEPENDENT OF THE THREE PHASE 2 DETECTORS.** Clean synthetic numbers below,
> with wide margins on most gates -- but "rising/falling over time" is the pattern synthetic
> timestamps simulate least realistically, and the aggregate scan's concentration thresholds
> (`MAX_TOTAL_VOLUME_PHASE_SHARE`) were tuned on N=2 examples per side. Trust batch-defect and
> fake-campaign's synthetic-clean results more than this one's until real-seller temporal data is
> available.

**REDEFINED SCOPE (2026-07-10), distinct from batch-defect -- not redundant with it.**
Batch-defect owns FAST negative clusters (a discrete event, then a short-window spike). This
detector now owns:
1. SLOW/GRADUAL shifts over LONG windows (a complaint creeping up over months).
2. POSITIVE trends (rising positive sentiment about a topic) -- entirely outside batch-defect's
   negatives-only scope.
3. AGGREGATE directional sentiment movement -- the product's overall sentiment ratio drifting,
   not tied to any single topic.

Three structural guards keep this from re-detecting batch-defect's job: CORRELATION_THRESHOLD
(clean monotonic shape), MIN_NON_DECREASING_STEPS (rejects "flat, flat, flat, huge jump"), and
MAX_PHASE_SHARE / MAX_TOTAL_VOLUME_PHASE_SHARE (rejects a fast cluster concentrated in one phase).
See `trend.py` module docstring for the full mechanism.

Moderation-prioritization signal only -- confidence + evidence for human review, never an
automated verdict that a product is in crisis or thriving.

## Original 18-product set (interim: keyword-proxy topic/sentiment tagging, full 837-review
coverage -- real-extraction validation still pending, see memory)

- Precision: 1.0, Recall: 1.0, False positive rate: 0.0
- True positives (2 planted trend products, 4 flags -- topic + aggregate both catch each): SYN-TREND-01, SYN-TREND-02
- False negatives (0): none
- False positives (0): none, including both matched controls and all 14 other non-trend-pattern
  products (batch-defect, fake-campaign, noise) -- explicitly checked

Flags: SYN-TREND-02/topic_negative/packaging (confidence 1.0), SYN-TREND-01/aggregate_negative
(confidence 1.0), SYN-TREND-01/topic_negative/delivery (confidence 0.875),
SYN-TREND-02/aggregate_negative (confidence 0.692).

**Confound history (found and fixed during this rebuild, not just assumed clean):** the first
version of the new aggregate scan false-positived on SYN-BATCH-01 (aggregate_negative, confidence
1.0 -- a late, concentrated batch-defect spike combined with shrinking later-phase review volume
produced a smoothly-rising negative RATIO despite being a single narrow event) and SYN-CAMPAIGN-02
(aggregate_positive, confidence 0.527 -- a short positive campaign burst landing mostly in one
phase, just under the match-count concentration cap). Both fixed by adding a TOTAL-volume
concentration guard (`MAX_TOTAL_VOLUME_PHASE_SHARE=0.35`) in addition to the match-count
concentration guard -- genuine trends in this testbed have ~0.29-0.30 max phase share of total
review volume; both confounds had ~0.42-0.45. Re-verified clean after the fix.

## Stress test (unseen patterns, thresholds finalized before this data existed)

- Precision: 1.0, Recall: 1.0 (3/3 planted trend patterns caught), False positive rate: 0.0
  (0/5 controls fired, including the dedicated random-fluctuation control)

| Product | Type | Shape | Correlation | Confidence |
|---|---|---|---|---|
| STRESS-TREND-SLOW (slow negative creep) | topic_negative | [3, 5, 5, 6] | 0.949 | 0.356 |
| STRESS-TREND-SLOW (same product, aggregate) | aggregate_negative | [0.278, 0.353, 0.389, 0.45] | 1.000 | 0.431 |
| STRESS-TREND-POSITIVE (rising positive) | topic_positive | [2, 3, 4, 6] | 1.000 | 0.500 |
| STRESS-TREND-AGGREGATE-DRIFT (no dominant topic) | aggregate_negative | [0.2, 0.3, 0.5, 0.7] | 1.000 | 1.000 |

Controls correctly NOT flagged: STRESS-CTRL-CHRONIC (flat, chronic complaint rate),
STRESS-CTRL-ORGANIC-BURST, STRESS-CTRL-POPULAR-SIMILAR, STRESS-CTRL-ORGANIC-BURST-SIMILAR-TEXT
(all fake-campaign-shaped controls), STRESS-CTRL-RANDOM-FLUCTUATION (bouncing negative-share, no
sustained direction). STRESS-BATCH-WEAK and STRESS-BATCH-MODERATE (fast defect spikes) also
correctly did NOT trigger trend -- direct evidence of no double-flagging with batch-defect's job.
