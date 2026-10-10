# Review-pattern detector: results (pre-registered evaluation)

Spec: `docs/specs/campaign-detection.md` (pre-registered; amendments 1-7a listed there).
All injected-campaign numbers are **SYNTHETIC**. Clean streams are **ASSUMED ORGANIC**, so
false-alert rates are upper bounds under that assumption. Sealed numbers come from ONE run
(`benchmark/campaign_eval/run.py sealed`, code and frozen parameters at commit `cde50cd`,
artifact `reports/campaign_eval/sealed_results.json`). Clean data: Amazon Fine Food Reviews
(CC0-1.0) and Sephora Skincare Reviews (CC BY 4.0, Melissa Monfared, Kaggle).

Streams: 94 Amazon (40567 reviews) + 158 Sephora (221904 reviews). Products per split: tuning 126, validation 51, sealed 75. Sealed: 75 products x 90 sealed configurations = 6750 injections.

## Verdict by the pre-registered decision rule

| Grid | Budget | Detector recall | Best baseline recall | Delta (paired 95% CI) | FAR <= budget | Wording test | Verdict |
|---|---|---|---|---|---|---|---|
| v2 (primary) | 1.0 | 0.880 | B1 0.782 | +0.098 [0.088, 0.109] | yes (0.060) | pass | USEFUL (opt-in eligible) |
| v2 (primary) | 0.1 | 0.880 | B1 0.775 | +0.105 [0.095, 0.117] | yes (0.060) | pass | USEFUL (opt-in eligible) |
| v1 (pre-registered grid) | 1.0 | 0.880 | B1 0.776 | +0.104 [0.093, 0.116] | yes (0.060) | pass | USEFUL (opt-in eligible) |
| v1 (pre-registered grid) | 0.1 | 0.880 | B1 0.773 | +0.107 [0.096, 0.119] | yes (0.060) | pass | USEFUL (opt-in eligible) |

The wording test is `tests/unit/test_campaign_wording.py` (passes at this commit). The budget
1.0 verdict is the pre-registered one; 0.1 is informational. 'Useful' means eligible for an
opt-in rollout behind `ENABLE_FAKE_CAMPAIGN_DETECTOR`, which stays OFF by default. It makes
**no claim about real-world recall** (see Limits).

## Sealed results, grid v2, budget 1.0 (SYNTHETIC injections)

Frozen detector parameters: `{"k_min": 3, "similarity": 0.4, "strong_multiplier": 1.5, "theta_burst": 3.0, "theta_rating": 4.0, "use_mismatch": false}`.

| Method | False alerts / product-month (95% CI) | Episodes | Recall (95% CI) |
|---|---|---|---|
| Detector | 0.060 [0.045, 0.078] | 273 | 0.880 [0.855, 0.903] |
| B1 z-score of daily count (z >= 0.0) | 0.185 [0.082, 0.318] | 834 | 0.782 [0.752, 0.809] |
| B2 rating moving-average drop (>= 1.1) | 0.059 [0.039, 0.085] | 265 | 0.342 [0.309, 0.375] |
| Union B1 or B2, each at half budget (information only) | 0.243 [0.125, 0.397] | 1099 | 0.823 [0.793, 0.852] |

Sealed clean exposure: 4519 product-months over 75 products. The detector's false-alert rate is far below the 1.0 budget and equal at both budgets: the budget did not bind (amendment 7a).

### Recall by campaign size (95% bootstrap CI over products)

| Size | Detector | B1 | B2 |
|---|---|---|---|
| 5 | 0.776 [0.735, 0.814] | 0.625 [0.590, 0.658] | 0.189 [0.156, 0.224] |
| 8 | 0.815 [0.772, 0.853] | 0.629 [0.590, 0.665] | 0.256 [0.213, 0.297] |
| 12 | 0.895 [0.861, 0.924] | 0.787 [0.748, 0.821] | 0.292 [0.245, 0.340] |
| 20 | 0.916 [0.890, 0.940] | 0.869 [0.835, 0.901] | 0.290 [0.253, 0.326] |
| 35 | 0.941 [0.924, 0.956] | 0.893 [0.866, 0.919] | 0.550 [0.516, 0.582] |
| 50 | 0.937 [0.913, 0.957] | 0.890 [0.858, 0.918] | 0.475 [0.444, 0.502] |

Minimum detectable size at the budget (smallest tested size reaching the recall; second value requires the CI lower bound to reach it): detector 0.5 -> 5 / 5, 0.8 -> 8 / 12; B1 0.8 -> 20 / 20. Sizes below 5 were not tested and cannot alert (N_MIN = 5).

### Recall by campaign type (similarity / rating skew)

| Type | Detector | B1 | B2 |
|---|---|---|---|
| independent/all-1 | 0.835 [0.800, 0.868] | 0.739 [0.702, 0.777] | 0.542 [0.477, 0.602] |
| independent/all-5 | 0.846 [0.813, 0.877] | 0.828 [0.788, 0.865] | 0.000 [0.000, 0.000] |
| independent/mixed | 0.888 [0.848, 0.923] | 0.870 [0.827, 0.908] | 0.570 [0.513, 0.622] |
| near-identical/all-1 | 0.896 [0.865, 0.922] | 0.741 [0.710, 0.767] | 0.590 [0.530, 0.648] |
| near-identical/all-5 | 0.907 [0.877, 0.934] | 0.711 [0.673, 0.744] | 0.000 [0.000, 0.000] |
| near-identical/mixed | 0.910 [0.886, 0.933] | 0.867 [0.830, 0.899] | 0.432 [0.377, 0.490] |
| paraphrased/all-1 | 0.897 [0.864, 0.927] | 0.815 [0.772, 0.855] | 0.588 [0.525, 0.648] |
| paraphrased/all-5 | 0.880 [0.844, 0.911] | 0.800 [0.768, 0.829] | 0.001 [0.000, 0.004] |
| paraphrased/mixed | 0.876 [0.839, 0.907] | 0.748 [0.708, 0.785] | 0.377 [0.331, 0.424] |

### Time to detect (detected campaigns only; q25 / median / q75)

- Campaign reviews posted at or before the alert: [4.0, 5.0, 5.0] (as a fraction of campaign size: [0.14, 0.3, 0.6]).
- Hours from the first campaign review: [0.53, 2.94, 15.29] (evaluated at review arrivals; a periodic sweep adds up to its interval; clean-stream intraday time is jittered).

### Alert precision (MODELLED, depends on an assumed prevalence)

precision = recall x rho / (recall x rho + false-alert rate): 0.548 at rho = 1 campaign per 12 product-months, 0.288 at 1 per 36. Not a measurement.

## Template signal, pairwise (sealed products; positives SYNTHETIC)

Pairs: {'negative_hard_top_similarity': 140, 'negative_random': 3213, 'negative_short_generic': 79, 'positive_near_identical': 3298, 'positive_paraphrased': 3160}. Median similarity: {"hard": 1.0, "pos_nid": 0.871, "pos_par": 0.512, "rand": 0.046, "short_gated": null, "short_ungated": 0.0}.

| s | recall paraphrased | recall near-identical | FP random same-product pairs | precision at 1:10 | FP real top-similarity pairs |
|---|---|---|---|---|---|
| 0.4 | 0.679 [0.663, 0.695] | 1.000 [0.999, 1.000] | 0.0009 (3/3213) | 0.989 | 140/140 |
| 0.5 | 0.522 [0.505, 0.540] | 1.000 [0.999, 1.000] | 0.0009 (3/3213) | 0.988 | 126/140 |
| 0.6 | 0.312 [0.296, 0.328] | 0.998 [0.996, 0.999] | 0.0009 (3/3213) | 0.986 | 118/140 |
| 0.7 | 0.134 [0.122, 0.146] | 0.945 [0.936, 0.952] | 0.0006 (2/3213) | 0.989 | 107/140 |

Frozen similarity: {"v1": {"0.1": 0.4, "1.0": 0.4}, "v2": {"0.1": 0.4, "1.0": 0.4}}. Wilson intervals; pairs inside a
product are correlated, so they are optimistic. The 'real top-similarity pairs' are the 10 most
similar real pairs per product; 76% are still >= 0.7 similar, which suggests duplicated text
already present in the corpora (BELIEVED, individual pairs not inspected). They did not drive the
clean-stream alerts (see exploratory section). Short-text gate: the sealed sample held only 79 short same-window pairs (0 false positives gated or not, upper bound ~4.6%), so
the gate's necessity is NOT demonstrated here. Exploratory (tuning products, not pre-registered): short reviews are 0.44% of these corpora; ungated, 0.62% of random same-product short pairs reach s = 0.4. These corpora are long-review; Flipkart/Hinglish seller data is short-review heavy and could not be tested (no timestamps).

## Exploratory (not pre-registered, validation split only)

Production rows carry no rating today. Frozen detector with the rating signals masked, validation split: recall 0.870 [0.845, 0.893] at 0.052 false alerts / product-month, versus 0.880 [0.858, 0.901] at 0.055 with ratings; B1 on the same split 0.808 at 0.176. The tuned rating threshold sat at the grid's strictest value (4.0), so the rating signal adds ~1 point: the shipped behaviour is mostly burst + template. Signal (d) mismatch was not enabled (ablation gain < 1 pp).

Clean-stream alerts (validation split, production `scan_stream` with the shipped defaults): 148 episodes over 2714 product-months = 0.0545 per product-month, identical to the harness's validation figure (0.0545), which cross-checks harness against production code. Signals fired on them: {"burst": 136, "burst+rating_shift": 6, "rating_shift": 6} - i.e. almost all are the burst signal alone at >= 1.5x its threshold; the template signal never fired.

## Limits (D2c and the rest)

- Synthetic campaigns test the attacks we modelled (size 5-50, 1h-7d, three text-similarity levels, three skews, uniform posting times), not unseen ones; real campaigns differ. No claim about real-world recall follows from any number here.
- The clean streams may contain real campaigns; false-alert rates are upper bounds under 'organic' and cannot see intraday clustering (dates are day-resolution, jittered uniformly).
- Both corpora are long-review marketplaces (food, skincare). Short, vernacular, Hinglish review streams (the target seller) are untested.
- The tuned template threshold (s = 0.4) is the grid's loosest value, and the selected row sits at the loose edge of v1 on four dimensions; v2 shows recall plateaus rather than rising, so the plateau is set by N_MIN = 5 and the signals, not by the budget.
- On clean streams the template signal did not fire at all (see exploratory section); its precision on real duplicated text under a burst is therefore untested beyond the pairwise numbers.
- The verified-buyer signal and signal (d) have no measured effect (absent / disabled); unit-tested only.
- Grid v2 was added after seeing tuning results (amendment 7); the detector pick did not change, the B1 pick did.
- A 7-day cooldown can re-alert once on a long campaign; production latency adds the sweep interval.
- Production plumbing gaps: ratings and `verified` are not in `list_dated_extractions_pg`; the internal names (`fake_campaign`, `ENABLE_FAKE_CAMPAIGN_DETECTOR`) still contain 'fake' (renaming the DB enum needs a migration; user-facing text does not). The sibling `likely_fake` / `fake_cluster` authenticity alerts still label individual reviews and are out of scope.

## Reproduce

```
# repo root; engine venv has numpy/pandas; PYTHONPATH=. ; corpora at D:\ml-cache\review-corpus
python -m benchmark.campaign_eval.run prepare          # qualify, split (seed 42), verbatim-copy check
python -m benchmark.campaign_eval.run tune --grid v1   # pre-registered grid
python -m benchmark.campaign_eval.run tune --grid v2   # amendment 7 grid (primary)
python -m benchmark.campaign_eval.run sealed          # ONCE; refuses to rerun
python -m benchmark.campaign_eval.run pairs           # ONCE
python -m benchmark.campaign_eval.render_report       # this file
pytest tests/unit/test_campaign_signals.py tests/unit/test_campaign_detector.py \
       tests/unit/test_campaign_wording.py tests/unit/test_campaign_generator.py \
       tests/unit/test_campaign_eval_harness.py tests/unit/test_campaign_eval_analysis.py
```
