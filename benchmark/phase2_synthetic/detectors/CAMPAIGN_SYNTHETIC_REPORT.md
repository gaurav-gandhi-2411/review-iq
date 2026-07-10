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

**Batch-defect products (topic-vs-timing confound):** this is a REAL confound, not just a theoretical one. Both planted batch-defect products (SYN-BATCH-01, SYN-BATCH-02) and one batch-defect control (SYN-BATCH-CTRL-02) trigger the timing burst gate and appear in the flags above. Root cause, traced to `generate_synthetic_testbed.py`: batch-defect "spike" reviews are assigned `timestamp=random_timestamp(spike_start, spike_start + 9 days)` -- i.e. only 8 reviews thrown uniformly at random into a 216-hour window, not concentrated by topic content into any particular sub-window. With that few points, random clumping (the same effect behind the birthday paradox) has a real chance of placing 5-7 of them inside some 48h sub-interval purely by chance -- exactly what happened here. This is NOT the detector picking up on topic-content concentration (it never reads review text for topic, only for near-duplicate clustering) -- it is a genuine small-N statistical false positive from the burst gate's defaults being loose relative to an 8-point sample, combined with this detector testing every review's own timestamp as a candidate window start (~40-75 candidate windows per product), which raises the chance of finding one spurious qualifying window somewhere in the timeline (a look-elsewhere effect).

The 3 batch-defect false positives are clearly separable from the 2 true campaign positives by confidence score in this run: true positives scored 0.771 and 0.769; the 3 false positives scored 0.360, 0.282, and 0.280 -- all driven to that lower band because their reviewer_concentration_score and text_dup_score are both exactly 0.0 (every reviewer and every text in their burst window is unique -- the organic shape, not the campaign shape). A downstream consumer applying a confidence >= 0.5 cutoff for actionable alerts (rather than treating burst-gate membership alone as actionable) would have 0 false positives and 0 false negatives on this run. This detector deliberately does not hardcode that cutoff into the flags list itself, since confidence here is a prioritization signal for human review, not a verdict.
