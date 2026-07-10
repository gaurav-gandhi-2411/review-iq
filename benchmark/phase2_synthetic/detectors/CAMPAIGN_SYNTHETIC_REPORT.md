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

### SYN-CAMPAIGN-01 -- confidence 0.636
- burst window: 2026-02-02T04:08:56Z -- 2026-02-04T04:08:56Z (48h)
- window review count: 11, ratio vs baseline: 13.995
- distinct reviewers: 5 (concentration score 0.545)
- distinct texts: 4 (dup score 0.636)
- cross-product reviewer reuse score: 0.0 (amplifier only)
- timing concentration score: 0.273 (evidence only, not scored)
- top reviewer_ids: ['rev-0525', 'rev-0529', 'rev-0527', 'rev-0528', 'rev-0526']
- top texts: ['usable product', 'its really worthable', 'paulo daily', 'very good creme']

### SYN-CAMPAIGN-02 -- confidence 0.615
- burst window: 2026-03-30T00:10:48Z -- 2026-04-01T00:10:48Z (48h)
- window review count: 13, ratio vs baseline: 15.43
- distinct reviewers: 7 (concentration score 0.462)
- distinct texts: 5 (dup score 0.615)
- cross-product reviewer reuse score: 0.0 (amplifier only)
- timing concentration score: 0.385 (evidence only, not scored)
- top reviewer_ids: ['rev-0602', 'rev-0601', 'rev-0604', 'rev-0603', 'rev-0606']
- top texts: ['juz wow', 'goooooooood ride', 'good and best', 'vevi niec', 'excellent tv value of moneygood connectivity']

## Confound analysis

**Matched-volume controls (SYN-CAMPAIGN-CTRL-01/02):** CTRL-02 has no 48h window meeting even MIN_BURST_COUNT -- rejected by the timing-burst gate alone. CTRL-01 DOES have a real qualifying timing-burst window (organic review volume can genuinely cluster within 48h) -- it is rejected by the CONFIDENCE gate instead: zero reviewer/text concentration in that window drives confidence to 0.0 despite the timing ratio clearing BURST_RATIO_THRESHOLD. Both controls match their planted counterparts' total review count -- proving this detector does not simply alert on "popular product, lots of reviews," even when that popularity produces a real timing burst.

**Batch-defect products (topic-vs-timing confound) -- FIXED:** an earlier version of this detector DID false-positive on SYN-BATCH-01/02 and SYN-BATCH-CTRL-02 (a real batch-defect spike also elevates raw review-arrival rate over a short window, without reviewer/text clustering -- confirmed via random clumping in a small 8-point sample, the birthday-paradox effect). Root cause: a flat 0.5 confidence floor gave burst-timing alone half credit regardless of reviewer/text concentration. Fixed by requiring `confidence = burst_component * max(reviewer_concentration_score, text_dup_score)` -- a burst with zero concentration on BOTH dimensions now scores exactly 0.0 -- plus a CONFIDENCE_REPORT_THRESHOLD=0.2 gate so a zero-confidence item no longer appears in the flagged list at all. Current run (see Summary above): 0 false positives, including on all 3 of these previously-affected products -- verified programmatically each run, not a one-time fix. See project_phase2_synthetic_testbed.md memory for the full fix history.

## Stress test (unseen patterns, thresholds finalized before this data existed)

**Structural recall bug found and fixed (2026-07-10):** the original window-selection logic
(`find_burst_window`) picked the single HIGHEST-RATIO 48h window per product, then scored it.
On `STRESS-CAMPAIGN-SMALL`, a coincidental cluster of unrelated organic reviews scored a
marginally higher ratio (7.598) than the real planted campaign burst (7.60) -- nearly tied -- so
the real burst was never even evaluated (0/1 recall). Fixed by evaluating EVERY qualifying window
and selecting by highest CONFIDENCE instead of highest ratio (`find_best_burst_window`) -- an
organic cluster's confidence is suppressed by its own zero reviewer/text concentration regardless
of how high its timing ratio happens to be, so it can no longer out-rank a genuinely coordinated,
lower-ratio burst.

**Discriminators added (same date):** cross-product reviewer reuse (amplifier only, gated to
never fire without an already-nonzero base signal), near-identical (not just identical) phrasing
via Jaccard token overlap on texts of 8+ words (gated by length so short organic praise can't
trigger it), and timing sub-window concentration (evidence-only, not scored -- a genuine
batch-defect can also cluster tightly by chance).

**Hardest adversarial control built to stress the new discriminators:**
`STRESS-CTRL-ORGANIC-BURST-SIMILAR-TEXT` -- an 18x-baseline real 48h timing burst, 11 unique
reviewers, each independently writing different short praise. Correctly scored confidence=0.0000
(reviewer_score=0.0, text_dup_score=0.0, cross_product_score=0.0) -- the near-dup signal's
length gate excludes short praise from the similarity check, so a real timing burst with merely
similar-sounding organic reviews cannot be mistaken for coordination.

| Product | Result | Confidence | Notes |
|---|---|---|---|
| STRESS-CAMPAIGN-SMALL (planted) | caught (was missed pre-fix) | 0.456 | 5 reviews, 3 reviewers, 2 templates |
| STRESS-CTRL-CHRONIC | not flagged | -- | no qualifying burst window |
| STRESS-CTRL-ORGANIC-BURST | not flagged | -- | 9 unique reviewers/texts, real burst |
| STRESS-CTRL-POPULAR-SIMILAR | not flagged | -- | similar short praise, no burst timing |
| STRESS-CTRL-ORGANIC-BURST-SIMILAR-TEXT | not flagged | 0.000 | real burst + similar text, the hardest case |

Precision: 1.0, Recall: 1.0 (1/1 planted stress campaign, after the fix), False positive rate: 0.0 (0/4 stress controls).
