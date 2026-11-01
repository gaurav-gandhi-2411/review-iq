# Q4 pre-registration - fake-review detection eval

Registered: 2026-10-08, before any run. Status: NOT RUN. This file is frozen once the first run starts; any later change goes in a dated "Amendments" section at the bottom with the reason, never as an edit to the sections above. Context: [q4-fake-review-scoping.md](q4-fake-review-scoping.md).

No claim about our own detection quality exists or is made here. Every number below is a design parameter (a threshold, a sample size, a cost estimate) chosen before seeing data.

## 1. Question and hypotheses

Question: can a text-and-optional-rating scorer flag fabricated reviews on unseen data precisely enough to show a seller?

- H1 (feature): on a held-out, different-domain set, the best arm flags with high precision and a very low rate of flagging genuine reviews, beating the strongest cheap baseline.
- H2 (hint): weaker evidence is enough for a flag-only "worth a look" hint that never states a verdict.
- H0: the LLM arm does not beat the cheap arms by a margin, or no arm reaches the hint bar on cross-domain data.

## 2. Datasets and why

Precondition (step 0, before any run): read the license text for each dataset in a browser and paste it verbatim into the Amendments section. A dataset whose license cannot be read, or that is research-only, is dropped and the run proceeds without it. The two fetches that failed to render were the Aston/Ott record and the OSF page for the Salminen set.

| Id | Dataset | Role | Why |
|---|---|---|---|
| D1 | Ott et al. Deceptive Opinion Spam v1.4 (1,600 hotel reviews, human-written fakes) | Test for human-written fabrication; training source for the TF-IDF arm in the cross-domain direction | Only clean-looking set with human-authored fakes. Wrong domain, which is the point of using it as a shift test |
| D2 | Salminen et al. Amazon Fake Review set (40,000, machine-generated fakes) | Product-domain test and training source | Product reviews in 10 categories. Fake type is machine text, so a high score here is weak evidence for paid human fakes |
| D3 (conditional) | Any clearly commercial-clean, human-labelled product-review set found in the second search pass | Added as a third test only if license is read and clean | Closest to deployment |
| Excluded | Yelp Open Dataset, YelpChi/NYC/Zip, Amazon 2018 and 2023 dumps | Not used | Educational-use statement (Yelp) or no cleared license; filter-inferred labels teach imitation of Yelp's filter |

Headline metric uses the cross-domain direction only: fit or prompt-tune on one dataset, test on the other. In-domain numbers are reported beside it but cannot justify any ship decision, because deployment is zero-shot on an unseen marketplace.

Not covered by any dataset: Hindi, Hinglish, incentivised-but-real reviews, competitor attacks. These are stated limits of every result, and a pass cannot be worded as covering them.

## 3. Split (seed 42)

Seed 42 for every random operation (numpy `default_rng(42)`, `random.seed(42)`, sklearn `random_state=42`, bootstrap resampling).

- D1: split by hotel, not by review, because reviews of one hotel share vocabulary. 20 hotels; draw 5 held-out hotels with `rng.choice(sorted(hotel_ids), 5, replace=False)`. Test = those 5 hotels = 400 reviews (200 deceptive, 200 truthful, by construction of 20 per cell per hotel). Development = 15 hotels = 1,200 reviews. The test split is touched once per arm after all arms and thresholds are frozen.
- D2: split by product category, because no reviewer or product id is guaranteed. 10 categories; draw 2 held-out categories by the same procedure. Everything in the 2 categories is test (about 8,000 reviews if categories are balanced; actual count recorded). Test is then subsampled to 1,000 reviews (500 per label) with seed 42 for the LLM arm so cost is bounded; the cheap arms also report on the full held-out categories.
- Leakage controls applied before splitting, with counts logged: exact-duplicate removal, then near-duplicate removal (3-word shingle Jaccard at or above 0.8) across the split boundary, dropping from the test side. The Ott set has a known property that the 20-per-cell design makes hotel-level leakage the main risk; the Salminen set may contain near-duplicate generated text.
- Development data is used only for the TF-IDF fit, the choice of operating point for each arm, and prompt freezing. Prompts and operating points are frozen and committed before test is read. Threshold selection uses development data only, via 5-fold cross-validation grouped by hotel or category.

Base-rate reporting: both test sets are balanced, a seller export is not. Report precision both as measured and re-weighted to assumed fake prevalences of 0.02, 0.05 and 0.10 via Bayes from the measured true-positive and false-positive rates (these are scenario values, not estimates of any real prevalence). The ship rule in section 6 uses the re-weighted precision at 0.05 as well as the raw numbers.

## 4. Arms (baseline ladder)

Each arm outputs a score in [0, 1] and, via a development-chosen cut, one of flag, clear or abstain (abstain = score inside a band around the cut, band width chosen on development data to hit target coverage of 0.8 non-abstain).

| Arm | Description | Inputs | Fit |
|---|---|---|---|
| A0 majority / trivial | Always predicts the development majority class; also a seeded random scorer with the development base rate | none | none |
| A1 length plus rating heuristic | Logistic regression on word count, character count, exclamation count, and star rating when present (D2 only; for D1 the rating feature is absent and the arm is length-only) | text, rating | dev |
| A2 legacy heuristics | The removed engine's `compute_heuristic_score` only (no LLM), run as shipped, with its own hand-set weights | text, rating | none (frozen code at commit bae3014) |
| A3 TF-IDF plus logistic regression | Word 1-2gram and char 3-5gram TF-IDF, L2 logistic regression, C chosen by grouped CV | text | dev |
| A4 LLM zero-shot | The removed prompt (`app/core/prompts/authenticity.py`), one model, temperature 0, single pass, no few-shot, JSON score. Model is a free or local one (Ollama or free-tier provider); name and version recorded at run time | text | none |
| A5 legacy full blend | The removed `score_single` blend of A4 and A2 with its hand-set 0.4/0.6 weights and cut points | text, rating | none |

A5 is run to answer "was the shipped design better than its parts", which was never tested. If A4 or A5 is not better than A3 by the margin in section 6, the LLM is not justified for this task.

LLM runs use VCR-style cassettes per repo rule: record once explicitly, replay for everything else, no live calls in CI. Prompt version is bumped and noted in PROMPTS.md per the repo's prompt-change checklist if the prompt text changes at all from the removed version (it should not).

## 5. Metrics

Definitions (positive = fabricated, flagged = system says flag):

- Precision at fixed recall: fix recall at 0.30, 0.50 on the test set by choosing the cut on development data; report test precision at those operating points. Also report the full precision-recall curve and average precision.
- Abstention-aware coverage: coverage = non-abstain share; answered precision = precision among flagged; report both and the pair at each operating point.
- Wrong-committed rate (primary safety metric): among genuine reviews, the share flagged. Equivalent to false positive rate. This is what a seller would experience as "you accused a real customer".
- Also report: AUROC, and per-language slice (English only, since no other language is present; Hinglish is explicitly untested).

Primary metric for decisions: test precision at recall 0.30 on the cross-domain direction, with its Wilson lower bound, together with the wrong-committed rate and its Wilson upper bound. Secondary: same at recall 0.50.

## 6. Decision rule (fixed in advance)

All intervals are 95 percent. "Cross-domain" means the better of D1-to-D2 and D2-to-D1 is not allowed to be picked after the fact: both directions must meet the bar for the same arm, and the weaker direction decides.

| Outcome | Condition (all must hold, on cross-domain test, at recall 0.30) |
|---|---|
| Ship as a feature | Wilson lower bound of precision at or above 0.90 with at least 100 flagged reviews; Wilson upper bound of wrong-committed rate at or below 0.05; re-weighted precision at prevalence 0.05 point estimate at or above 0.50; paired-bootstrap lower bound of the difference in average precision versus the best of A0 to A3 is above zero; AND an in-domain labelled sample (not available today) has been evaluated with the same rule. Without the in-domain sample the best this study can authorise is the next row |
| Ship as a flag-only hint | Wilson lower bound of precision at or above 0.80 with at least 60 flagged; Wilson upper bound of wrong-committed rate at or below 0.10; beats best of A0 to A3 by the paired-bootstrap rule. Copy must say "worth a look", never state a verdict, show the flag reason, and be off by default. Retains the earlier precision-first response contract (no words fake, genuine, suspicious in the API) |
| Do not ship | Anything else |

The numeric bars are judgement calls, not derived from data: a hint that points at a real customer more than once in ten flags is, in my judgement, too costly for a trust feature. Changing them after seeing results is a protocol violation.

## 7. Confidence intervals

- Proportions (precision, recall, wrong-committed rate, coverage): Wilson score interval, z = 1.96, using the repo's `eval/wilson.py`.
- Differences between arms (average precision, AUROC, precision at fixed recall): paired bootstrap over test reviews, 2,000 resamples, seed 42, percentile interval. Resampling unit: the review for D2; the hotel (5 clusters) cannot support a cluster bootstrap, so for D1 report review-level bootstrap and flag that hotel clustering makes intervals optimistic.
- Multiple comparisons: one primary comparison (best arm versus best of A0 to A3 at recall 0.30); everything else is labelled exploratory.
- Report n at every table cell. Report abstain and parse-failure counts for LLM arms next to every score; a parse failure counts as abstain, never as clear.

## 8. Minimum n and power

Computed with the Wilson interval (`python`, formula in `eval/wilson.py`), true-precision scenarios rounded to whole counts:

- A flagged set of 60 with true precision 0.92 gives a 95 percent Wilson interval of about 0.82 to 0.96. Lower bound clears 0.80 (hint bar) only with room to spare when true precision is 0.92 or better. So 60 flagged is the minimum for the hint decision.
- A flagged set of 100 with true precision 0.97 gives a lower bound about 0.915, clearing 0.90. A flagged set of 100 at true precision 0.92 gives about 0.85, which would not clear the feature bar, correctly. So 100 flagged is the minimum for the feature decision, and the feature bar is only reachable if true precision is about 0.97 or better.
- Recall estimate: with 150 positives and true recall 0.30 to 0.50, the Wilson half-width is about 0.08. Hence at least 150 positives per test set.
- Planned test sizes: D1 has 200 positives (meets 150); at recall 0.30 it yields about 60 flagged at best, so D1 can support the hint decision but only just supports the feature minimum at recall 0.50 (about 100 flagged). D2 subsample has 500 positives, giving about 150 flagged at recall 0.30, enough for either decision.
- Wrong-committed bar: an upper bound at or below 0.05 needs zero to few false flags among 200 genuine: with 200 genuine reviews, 3 false flags gives an upper bound near 0.043 and 5 gives near 0.057 (Wilson), so D1 can demonstrate the 0.05 bar only if at most about 3 genuine reviews are flagged. This is why D2 (500 genuine) carries the safety decision.
- If realised flagged counts fall below the minimums, the outcome is "insufficient n", which maps to do not ship, not to a relaxed bar.

## 9. What falsifies the idea

Any one of the following, as observed on the pre-registered test, falsifies "a text scorer is shippable as a seller-facing fake-review feature with the current resources":

1. Best arm's cross-domain precision lower bound at recall 0.30 is below 0.80, in either direction.
2. A4 and A5 (the LLM arms) do not beat A3 (TF-IDF) by the paired-bootstrap rule, which would say the model adds cost without value; the cheap arm then becomes the only candidate.
3. Wrong-committed upper bound above 0.10 for every arm that meets the precision bar.
4. A2 (the removed heuristics) does not beat A0 or A1, which would say the shipped signal set carried no information and should stay removed.
5. Performance collapses from in-domain to cross-domain (a large gap between the two directions), which says the signal is dataset artifact, the same failure that recon already found for the Flipkart repeat clusters.

A pass cannot, by itself, prove the idea: both datasets are proxies, balanced, English-only.

## 10. Cost estimate per arm

Token counts are estimates from prompt length (about 450 input and 60 output tokens per call including the system prompt and a typical review); the money line must be filled from `app/core/pricing.py` at run time, not from memory.

| Arm | Compute | Calls | Tokens (est.) | Cost |
|---|---|---|---|---|
| A0 | CPU, seconds | 0 | 0 | 0 |
| A1 | CPU, seconds | 0 | 0 | 0 |
| A2 | CPU, seconds | 0 | 0 | 0 |
| A3 | CPU, minutes (TF-IDF on tens of thousands of short texts) | 0 | 0 | 0 |
| A4 | 1,400 test calls (400 D1 plus 1,000 D2), plus development prompt-check on 200 calls | 1,600 | about 0.72M in, 0.10M out | 0 on local Ollama or a free tier; free-tier daily token budgets are a real limit, so split across days and never run an unrecorded live batch in CI |
| A5 | Re-uses A4 outputs plus A2 | 0 extra | 0 | 0 |
| Cassette recording | Same calls as A4 | included | included | 0 |

Total expected spend: 0 USD. Paid-provider spend is out of scope and needs explicit approval; if a free tier cannot cover the 1,600 calls, stop and report rather than switch to a paid model.

## 11. Run protocol

1. Amend this file with verbatim license text for D1 and D2 (step 0). Drop any set that fails.
2. Build splits with seed 42; commit the split manifests (ids only) and the leakage-removal counts.
3. Freeze arms A0 to A5 code, prompt and development-chosen operating points; commit.
4. Run test once per arm; write results to `eval/results/` with commit SHA and artifact path in the report (repo rule on metric provenance); no number appears in README or site copy unless produced by the committed run and injected by the metrics pipeline.
5. Evaluate section 6 mechanically from the results file. Report negative results in full.
6. Do not re-run on the test set to tune; any extra run is labelled exploratory.

## Amendments

(none yet)
