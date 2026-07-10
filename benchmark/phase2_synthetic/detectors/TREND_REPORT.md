# Phase 2 Trend Detector -- Flagged Products

> SYNTHETIC-VALIDATED DETECTOR -- see `trend.py` module docstring. NOT proven against real
> seller data.
>
> **MOST REAL-DATA-DEPENDENT OF THE THREE PHASE 2 DETECTORS.** Clean synthetic numbers below,
> with wide margins on both gates -- but "rising over time" is the pattern synthetic timestamps
> simulate least realistically. Trust batch-defect and fake-campaign's synthetic-clean results
> more than this one's until real-seller temporal data is available.

Moderation-prioritization signal only -- confidence + evidence for human review, never an
automated verdict that a product is in crisis.

## Summary (interim: keyword-based topic/sentiment tagging against the ground truth's own
sourcing criteria, full 837-review coverage -- real-extraction validation still pending, see
memory)

- Precision: 1.0, Recall: 1.0, False positive rate: 0.0
- True positives (2): SYN-TREND-01, SYN-TREND-02
- False negatives (0): none
- False positives (0): none, including both of trend's own matched controls and all 14 other
  non-trend-pattern products (batch-defect, fake-campaign, noise) -- explicitly checked

## Margin check (not just pass/fail -- how close was it?)

| Product | Phase counts | Rise (need >=3) | Correlation (need >=0.8) |
|---|---|---|---|
| SYN-TREND-01 (planted) | [1, 3, 5, 8] | 7 | **1.000** |
| SYN-TREND-02 (planted) | [2, 4, 6, 10] | 8 | **1.000** |
| SYN-TREND-CTRL-01 (control) | [5, 4, 4, 4] | -1 | **-0.775** |
| SYN-TREND-CTRL-02 (control) | [9, 5, 3, 6] | -3 | **-0.400** |

Both true positives clear the correlation gate with the maximum possible margin (perfect
correlation, 0.2 above threshold). Both controls land with NEGATIVE correlation -- nowhere
close to the 0.8 bar. This is a wide, robust separation, not a narrow pass.

## Flagged products

### SYN-TREND-01 / delivery -- confidence 0.875
- phase_counts: [1, 3, 5, 8] (correlation=1.0)
- "'delivery' complaints rising: 1 -> 3 -> 5 -> 8 across 4 phases (correlation=1.0)"

### SYN-TREND-02 / packaging -- confidence 1.0
- phase_counts: [2, 4, 6, 10] (correlation=1.0)
- "'packaging' complaints rising: 2 -> 4 -> 6 -> 10 across 4 phases (correlation=1.0)"

## Correctly NOT flagged (controls)

### SYN-TREND-CTRL-02 -- same total volume as its planted counterpart (22 vs 22), flat not rising
- weekly_topic_counts: [2, 2, 2, 2, 1, 2, 1, 1, 1, 1, 1, 1, 1, 2, 1, 1]
- phase sums: [8, 5, 4, 5] -- no rise

### SYN-TREND-CTRL-01 -- 17 total complaints, flat
- phase sums: [5, 4, 4, 4] -- no rise
