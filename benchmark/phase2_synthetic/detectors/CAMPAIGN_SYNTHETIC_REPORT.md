# Campaign Synthetic Detector -- Validation Report

SYNTHETIC-VALIDATED DETECTOR. Results below are against a synthetic testbed with PLANTED, KNOWN ground truth (`benchmark/phase2_synthetic/`). NOT proven against real seller data. Do not quote these numbers as real-world accuracy.

## Summary

- Precision: 1.000
- Recall: 1.000
- False positive rate: 0.000
- True positives (2): SYN-CAMPAIGN-01, SYN-CAMPAIGN-02
- False negatives (0): none
- False positives (0): none
- True negatives (16): SYN-BATCH-01, SYN-BATCH-02, SYN-BATCH-CTRL-01, SYN-BATCH-CTRL-02, SYN-CAMPAIGN-CTRL-01, SYN-CAMPAIGN-CTRL-02, SYN-NOISE-01, SYN-NOISE-02, SYN-NOISE-03, SYN-NOISE-04, SYN-NOISE-05, SYN-NOISE-06, SYN-TREND-01, SYN-TREND-02, SYN-TREND-CTRL-01, SYN-TREND-CTRL-02

## All flagged products

### SYN-CAMPAIGN-02 -- confidence 0.615
- burst window: 2026-03-30T00:10:48Z -- 2026-04-01T00:10:48Z (48h)
- window review count: 13, ratio vs baseline: 15.43
- distinct reviewers: 7 (concentration score 0.462)
- distinct texts: 5 (dup score 0.615)
- top reviewer_ids: ['rev-0602', 'rev-0601', 'rev-0604', 'rev-0603', 'rev-0606']
- top texts: ['juz wow', 'goooooooood ride', 'good and best', 'vevi niec', 'excellent tv value of moneygood connectivity']

### SYN-CAMPAIGN-01 -- confidence 0.583
- burst window: 2026-02-01T12:15:15Z -- 2026-02-03T12:15:15Z (48h)
- window review count: 12, ratio vs baseline: 15.267
- distinct reviewers: 6 (concentration score 0.5)
- distinct texts: 5 (dup score 0.583)
- top reviewer_ids: ['rev-0525', 'rev-0529', 'rev-0527', 'rev-0528', 'rev-0526']
- top texts: ['usable product', 'its really worthable', 'paulo daily', 'very good creme', 'good product quality thanks flipkart']

## Confound analysis

**Matched-volume controls (SYN-CAMPAIGN-CTRL-01/02):** the timing-burst GATE alone rejects both, with no ambiguity -- neither product has ANY 48h window meeting even MIN_BURST_COUNT, let alone BURST_RATIO_THRESHOLD (best ratio found: 0.0 for both, i.e. no qualifying window at all). Their reviews are spread evenly across the full ~16-week observation period despite matching their planted counterparts' total review count -- proving this detector does not simply alert on "popular product, lots of reviews."

**Batch-defect products (topic-vs-timing confound) -- FIXED:** an earlier version of this detector DID false-positive on SYN-BATCH-01/02 and SYN-BATCH-CTRL-02 (a real batch-defect spike also elevates raw review-arrival rate over a short window, without reviewer/text clustering -- confirmed via random clumping in a small 8-point sample, the birthday-paradox effect). Root cause: a flat 0.5 confidence floor gave burst-timing alone half credit regardless of reviewer/text concentration. Fixed by requiring `confidence = burst_component * max(reviewer_concentration_score, text_dup_score)` -- a burst with zero concentration on BOTH dimensions now scores exactly 0.0 -- plus a CONFIDENCE_REPORT_THRESHOLD=0.2 gate so a zero-confidence item no longer appears in the flagged list at all. Current run (see Summary above): 0 false positives, including on all 3 of these previously-affected products -- verified programmatically each run, not a one-time fix. See project_phase2_synthetic_testbed.md memory for the full fix history.
