# Review-pattern (campaign) detection: spec, pre-registered before any evaluation code runs

**Target user.** Sellers and D2C brands who sell on marketplaces and their own stores (Judge.me, Shopify, Flipkart, Amazon) and want to know when a product's review stream is being hit by a coordinated burst (a competitor's 1-star wave, a purchased 5-star wave, a copy-pasted template).

**Pain point.** A seller cannot see a pattern across hundreds of reviews until the rating has already moved, and cannot tell "a viral week" from "14 one-star reviews in 3 hours, 9 of them sharing near-identical text". They want a prioritised list of patterns to review and report to the marketplace, not a verdict.

**Success metric.** On a SEALED test set of products and sealed injection configurations: campaign recall at a fixed false-alert budget of at most 1 false alert per product per month on clean streams (secondary budget 0.1), with 95% bootstrap CIs resampling products, and a recall margin over the best of two simple baselines whose CI excludes zero. Every injected-campaign number is labelled SYNTHETIC.

**Who pays.** The seller or brand, per active store per month. The detector is a feature of the alerts product, not a product by itself. Not priced here.

Status: PRE-REGISTERED, 2026-10-11. Nothing below has been run on the evaluation data. Thresholds, splits and decision rules are fixed by this file's commit. Any later change is a dated amendment at the bottom of this file, never an edit above it, and the final report must list amendments.

---

## 0. Product rule (also enforced by a test)

The product flags PATTERNS across reviews: counts, time windows and shared text ("14 one-star reviews in 3 hours, 9 sharing near-identical text - review these"). It NEVER labels an individual review as fake or inauthentic. Reasons: single-review deception detection from text has no matching labelled data for this product; humans perform near chance on deceptive text; platforms rely on signals (purchase records, account graphs, device data) a seller-side tool cannot see.

Enforcement: a wording test fails if any user-facing string the detector can emit (alert subject, alert body, explanation, signal names, API field values) contains "fake" (any use), "fraud", "bot", "inauthentic", "not genuine", "scam", "counterfeit", or asserts that a named review is not real. Wording describes counts, windows and shared text.

## 1. Audit of the existing detector (summary; full evidence in the final report)

Existing code: `app/core/detectors/campaign.py` (production fork of `benchmark/phase2_synthetic/detectors/campaign_synthetic.py`), driven by `app/core/alerts/detector_sweep.py`, flag `ENABLE_FAKE_CAMPAIGN_DETECTOR` (default false).

What it computes: for each product, the best 48h window by `confidence = min(1, ratio/10) * max(reviewer_concentration, text_dup)`, where ratio is window count over the product's whole-history average rate (history includes the burst), text_dup is the max of exact-duplicate share and Jaccard-0.85 token near-duplicate share (8+ words only).

Findings that decide reuse versus replace:

1. `reviewer_id` is stubbed to the review's own id in production, so reviewer concentration and cross-product reuse are always 0 and the alert body prints "N distinct reviewer IDs" where N always equals the review count. REPLACE: that number is a meaningless statement presented as evidence.
2. Rating is not used at all. Rating skew and rating shift, the most seller-visible symptoms, are absent. ADD.
3. Exact-duplicate share applies to short generic text ("good product" repeated) with no length gate, only the near-dup branch is gated. REPLACE with one pre-registered short/generic policy (section 3b).
4. Baseline uses the whole history including the burst, a single fixed 48h window, no dispersion model. REPLACE with a trailing-history baseline, several window widths, and a product-specific dispersion estimate.
5. Validation evidence is 2 planted campaigns in a hand-built testbed (precision 1.0, recall 1.0 on n=2 positives) and no false-alert rate on real streams. Not a defensible performance claim. The existing alert threshold 0.5 was chosen on those cases.
6. Wording: "Possible coordinated review campaign", "distinct reviewer IDs", "confidence 0.xx", internal names containing "fake". The campaign alert text itself does not call a single review fake; the sibling `likely_fake` / `fake_cluster` authenticity alerts do, and are out of scope here (reported as a gap, they belong to the authenticity feature).
7. Earlier recorded work (project history, `benchmark/phase2_campaign/`): the licensed Flipkart corpus has NO timestamps and no reviewer ids, and the text-duplicate "campaign" signal there is mostly cross-source scraping artifact. So that corpus cannot evaluate burst or shift signals and is NOT used here.
8. Reusable as is: `Review` dataclass shape (extended), `normalize_cluster_text`, `campaign_reviews_from_rows` adapter, the alert-engine path (`precomputed_events`, dedupe ledger via a stable key), the global flag, the sweep isolation structure.

## 2. Detector design

Input per product: reviews with timestamp, optional rating (1-5), text, optional `verified` (bool or None). Evaluated "as of" each review arrival t_i using only reviews up to t_i (no look-ahead), for window widths W in {6h, 24h, 72h, 168h}. The window is (t_i - W, t_i]; history is the 180 days before the window start (H = 180 d).

A window is only evaluated if n_w >= N_MIN = 5 reviews and history has >= 15 reviews spanning >= 30 days (cold start: no alert, never a default). Warm-up for scoring: reviews before the first 100 reviews or first 60 days of a stream never open an alert in the evaluation.

Signals and their evidence values (each is "fired" when evidence >= its threshold):

(a) BURST. mu = history daily-mean rate x W. phi = max(1, variance/mean of the daily counts over the 90 days before the window), capped at 20 (product-specific overdispersion; quasi-Poisson scaling). Evidence e_b = -log10 P(Poisson(mu/phi) >= ceil(n_w/phi)), capped at 12. Needs mu floored at 0.05 to keep a silent product from producing infinite evidence.

(b) TEMPLATE. Text normalised (lowercase, punctuation stripped, whitespace collapsed). Reviews of fewer than 6 words or 30 characters are INELIGIBLE (policy 3b). For eligible reviews: set of hashed character 5-gram shingles; pair similarity = Jaccard; clusters = connected components of pairs with Jaccard >= s. Evidence e_t = size k of the largest cluster in the window. Fired when k >= K_MIN. Method justification: character n-gram Jaccard is robust to punctuation, typos and word swaps, needs no corpus statistics (a seller's few hundred reviews are too few for stable TF-IDF idf), is deterministic and dependency-free (production code is stdlib only). Exact pairwise Jaccard is used because windows hold at most a few hundred reviews; MinHash is the scale-up path if that stops being true. s is a parameter chosen on the tuning split only.

(c) RATING SHIFT. z_r = (mean_w - mean_h) / (sd_h * sqrt(1/n_w + 1/n_h)), with sd_h floored at 0.6 (ratings are discrete and bimodal; the floor stops a near-constant history from inflating z) and requires n_h >= 30. Two-sided; evidence e_r = |z_r|, direction reported.

(d) RATING-TEXT MISMATCH. A review mismatches if rating >= 4 and text lexicon net sentiment <= -2, or rating <= 2 and net >= +2 (small fixed positive/negative word lists in the code; a heuristic, not a sentiment model; on data without a text signal it is inert). Evidence e_m = z of window mismatch share against the history share (history share floored at 0.02), one-sided upward. Included in the alert rule ONLY if the ablation in section 7 shows it adds at least 1.0 absolute percentage point of tuning recall at the same budget; otherwise it is explanation-only. The ablation is run once on the tuning split and the outcome is reported either way.

(e) VERIFIED-BUYER SHARE (optional input; the Judge.me field is believed to be `verified`, UNCONFIRMED). Used only when >= 80% of window and history reviews carry a non-null value. e_v = z of the drop in verified share (window below history, one-sided). It is absent in both evaluation corpora, so it is covered by unit tests only and has NO measured effect in this evaluation. The detector must produce identical output with the field absent.

Alert rule (the combiner), applied per arrival and per W: with n_w >= N_MIN, alert if (number of fired signals among the enabled ones >= 2) OR (any single signal >= C x its fire threshold, C = strong multiplier, template strong means k >= ceil(C x K_MIN)). The firing W is the smallest W satisfying the rule; the explanation lists which signals fired and the counts. Cooldown: a new alert on the same product is only opened 7 days after the previous alert's time (episode definition, used for every false-alert count).

Parameters searched (exhaustive grid, tuning split only): s in {0.4, 0.5, 0.6, 0.7}; K_MIN in {3, 4}; theta_b in {3, 4, 5, 6}; theta_r in {2.5, 3.0, 3.5, 4.0}; C in {1.5, 2, 3}; theta_m fixed at 3.0 if (d) is enabled. 384 combinations. Fixed, not searched: N_MIN, windows, H, phi cap, floors, cooldown.

Evaluation points are review arrivals. The production sweep runs periodically (the scheduler interval adds up to that interval of latency); hours-to-detect below are therefore a lower bound on production latency.

## 3. Data and clean streams

Corpora (on disk, licensed): Amazon Fine Food Reviews (CC0-1.0; `Reviews.csv`: ProductId, Score, Time unix seconds, Text) and Sephora Skincare Reviews (CC BY 4.0; attribution required: Melissa Monfared, Kaggle `sephora-skincare-reviews`; `reviews_*.csv`: product_id, rating, submission_time, review_text). Attribution is carried into the results report.

Assumption stated plainly: these streams are treated as ASSUMED ORGANIC. They may contain real campaigns, incentivised-review waves and viral spikes. The measured false-alert rate is therefore an UPPER BOUND on the true false-alert rate under that assumption (a real campaign flagged here counts against the detector).

Qualification (fixed now): >= 300 reviews with non-empty text, and reviews in >= 24 distinct calendar months. Product key = `amazon:<ProductId>` or `sephora:<product_id>`.

Timestamps are DAY-resolution in both corpora (verified: every Time/submission_time value is at 00:00). Intraday structure is not observable. Rule: each clean review gets timestamp = date + U(0, 24h), drawn from `numpy.random.default_rng` seeded by `42 + crc32(stream_key + row_index)` (deterministic). Consequence: sub-day burst behaviour of clean streams is modelled as uniform arrivals within the day, an assumption, not an observation. Real clean streams have intraday clustering this evaluation cannot see, so sub-day false-alert rates may be understated and sub-day recall may be overstated.

3b. Short and generic text policy: reviews under 6 words or 30 characters are ineligible for the template signal ("great product", "nice", "good quality" cannot create a cluster). The template evaluation reports what would happen WITHOUT this gate on real short pairs, so the gate's necessity is measured, not asserted.

Split (products, not reviews): per corpus separately, sort qualifying product keys lexicographically, permute with `numpy.random.default_rng(42).permutation`, first 50% TUNING, next 20% VALIDATION, last 30% SEALED. Sealed products are used for nothing before the final run.

## 4. Synthetic campaign injection (every result from this is SYNTHETIC)

Generator `benchmark/campaign_eval/generator.py` (documented templates + paraphrase operations; no corpus text is copied; domain-neutral vocabulary because streams span food and skincare). Campaign = N reviews with times spread (uniformly at random, seeded) over a duration D starting at a random evaluable time of the stream (after warm-up, >= 14 days before the stream end), ratings per skew, texts per similarity level.

Grid: size N in {5, 8, 12, 20, 35, 50}; duration D in {1h, 6h, 24h, 72h, 168h}; similarity in {independent, paraphrased, near-identical}; skew in {all-1-star, all-5-star, mixed (each review independently 1 or 5 with p = 0.5)}. 6 x 5 x 3 x 3 = 270 configurations.
- near-identical: one template, 0-2 token edits (punctuation, one swapped word). Target Jaccard (char 5-gram) typically above 0.8.
- paraphrased: one template with several synonym / clause-reorder / filler operations per review. Target Jaccard typically 0.35-0.7.
- independent: each review composed independently from a pool of sentence fragments, so pairwise similarity is at organic level. The text signal is expected NOT to catch these; they test burst and rating signals only.
Text sentiment follows the rating (1-star campaigns write negative text, 5-star positive, mixed follows each review's rating), so the mismatch signal is expected to be largely inert on the injected campaigns; this is a modelling limitation to be reported.

Configuration split: for each (size, similarity) cell, the 15 (duration, skew) combinations are permuted with `default_rng(42)`; first 5 SEALED, next 3 VALIDATION, last 7 TUNING. Tuning = tuning products x tuning configs, validation = validation products x validation configs, sealed = sealed products x sealed configs (90 sealed configs). Each (product, config) pair gets exactly one injection, at a position drawn from `default_rng(42 + crc32(stream_key + config_id))`. For the tuning split, each tuning product is paired with 20 configs sampled (seeded) from the 126 tuning configs, to bound compute; validation and sealed use every config for every product of their split.

Excerpt rule (compute only): an injected stream is evaluated on the excerpt [t_inj - 187 d, t_inj + D + 8 d]; history needs only 180 d + the 7 d widest window, so this equals full-stream evaluation (checked by a test on a sample).

## 5. Metrics (alert level, never review level)

- Detection: an injected campaign is DETECTED if an alert opens at time t_a in [first campaign review, last campaign review + 24h] AND the firing window (for baselines: the baseline's own window) contains at least 3 campaign reviews. This stops an unrelated organic false alert from being credited.
- Campaign recall = detected / injected, by size and by type (similarity x skew), with 95% percentile bootstrap CIs resampling PRODUCTS (2000 resamples, seed 42; all injections of a resampled product are carried together).
- False alerts per product per month on clean streams: alert episodes (cooldown 7 d) after warm-up divided by evaluated months (days / 30.4375), pooled over the split's products, 95% bootstrap CI over products (same procedure).
- Alert precision: MODELLED, because precision depends on prevalence. precision = recall x rho / (recall x rho + FAR), for rho in {1/12, 1/36} campaigns per product-month. Reported as modelled with both rho, not as a measurement.
- Time to detect, for detected campaigns: (1) campaign reviews posted at or before the alert time (including the triggering one), as count and as fraction of campaign size; (2) hours from the first campaign review to the alert time. Median and IQR.
- Detection-versus-size curve (recall at each size) and minimum detectable size at the budget: smallest size whose recall >= 0.5 (and >= 0.8), with the CI-conservative version (CI lower bound >= the level).
- Template signal, evaluated separately, pairwise: positives = pairs from the same generated template at the paraphrased and near-identical levels (reported separately and pooled); negatives = real corpus pairs: (i) random same-product pairs within 7 days, (ii) hard negatives, the highest-similarity real pairs within products, (iii) short generic pairs (< 6 words), evaluated both with and without the eligibility gate. Precision/recall at the chosen s. Precision uses a fixed 1 positive : 10 negatives weighting, stated as such. Pairs drawn only from the split under report (sealed: sealed products' real text and sealed config seeds).

## 6. Baselines (same budget, same treatment)

B1. z-score on daily review count: count in trailing 24h versus mean and sd of daily counts over the preceding 90 days (sd floored at 0.5), alert if z >= theta_B1 and count >= 5. B2. Rating moving-average drop: mean of the last 10 reviews minus mean of the preceding 90 days of reviews, alert if drop >= theta_B2 (downward only, as specified; it cannot fire on 5-star waves, which is reported rather than hidden), same cold-start, warm-up and 7-day cooldown. Each baseline's single threshold is chosen by the same budget rule as the detector's parameters. The "best baseline" is whichever of B1 and B2 has the higher VALIDATION recall; the comparison is then made on SEALED. The union B1 or B2 (each tuned to half the budget) is reported for information only.

## 7. Threshold policy

Budget B (fixed before running): primary B = 1.0 false alerts per product per month; secondary B = 0.1. For each method and budget: rank parameter combinations by TUNING recall subject to tuning false-alert rate <= B; take the best combination whose VALIDATION false-alert rate is also <= B (if none, report "no combination meets the budget", recall 0). The ablation for signal (d) runs here. The sealed set is then run ONCE per method and budget with the frozen parameters; sealed numbers are reported once, including if unfavourable. No parameter, threshold, split or generator change after seeing sealed output; if a defect is found, it is reported as an amendment with the sealed numbers before and after.

## 8. Decision rule (pre-registered)

The detector ships behind `ENABLE_FAKE_CAMPAIGN_DETECTOR` as "useful" only if ALL hold at the primary budget B = 1.0 on the sealed set: (1) sealed recall (pooled over sealed injections) exceeds the best baseline's by a margin whose paired 95% bootstrap CI (resampling products, 2000, seed 42) has a lower bound above zero; (2) sealed false alerts per product per month (point estimate) <= 1.0; (3) the wording test passes. Otherwise the report says NOT SHIPPED with the numbers. Either way the flag stays off by default; "useful" means eligible for an opt-in rollout, not enabled. The 0.1 budget is reported alongside and is informational for the verdict, but a "useful at 1.0 but not at 0.1" outcome must be stated as such because 1.0 per product-month is a loose budget for a seller.

## 9. Stated limitations (D2c)

Synthetic campaigns test the attacks we modelled, not unseen ones; real campaigns differ (staggered posting, mixed ratings, edited text, aged accounts, review-gating). No claim about real-world recall follows from any injected-campaign number here. The clean streams are marketplace food and skincare, not the seller's own mix. The false-alert rate is an upper bound only under the assumption that these streams are organic, and a lower-bound-ish estimate for sub-day behaviour because intraday clustering is invisible. The lexicon mismatch heuristic is not validated. The verified-buyer signal is untested on real data. The production rows carry no rating and no reviewer identity today, so the rating signals are inactive in production until ingestion plumbs them; the measured performance applies to streams WITH ratings.

## Amendments

Amendments 1-6 were written on 2026-10-11 before the tuning or sealed runs, while the harness was being finished; they record implementation details the sections above left open. None changes a threshold, split, metric or decision rule.

1. Jitter (section 3): one `default_rng(42 + crc32(stream_key))` per stream, drawing U(0, 24h) in date-sorted row order, instead of one generator per review. Same determinism, far cheaper.
2. Excerpt rule (section 4): the excerpt starts 195 days before the injection (not 187) and evaluation points start 7 days before the injection. The 7-day pre-roll lets the cooldown state form from the organic week before the campaign, so an organic alert opened just before a campaign can suppress an alert on it, as it would in production. A unit test checks excerpt evidence equals full-stream evidence.
3. The fire threshold for the verified-share signal (e) is 3.0, same as mismatch; it is inert in this evaluation (field absent).
4. Qualification (section 3) yields 94 Amazon and 158 Sephora streams (252 total, 262,471 reviews). The split is 50/20/30 per corpus: tuning 126 streams, validation 51, sealed 75 (counts confirmed in `reports/campaign_eval/split.json`).
5. The sealed run evaluates only the frozen parameter columns written to `frozen_params.json` before it starts, and refuses to run twice.
6. Disclosure: before the split existed, a smoke test ran the full grid on the first stream key in sorted order (`amazon:B0007A0AQM`, clean span plus 6 tuning-configuration injections) to measure runtime. Its output was not used for any decision. Whether that stream lands in the sealed split is stated in the final report.
