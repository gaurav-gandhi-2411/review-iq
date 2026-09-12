# ADR 0026: Coverage, accuracy-on-answered, and wrong-committed at n=106 — the abstention story weakens for buy_again

## Context

Session 11 reported the held-out corpus's flat accuracy (68.3%, n=106) but never delivered the
coverage / accuracy-on-answered / wrong-committed breakdown the brief explicitly asked for again
this session — flat accuracy is the weakest possible framing of an abstaining extractor (a model
that hedges 60% of the time and is perfect on the other 40% scores identically, by this metric
alone, to a model that never hedges and is wrong 32% of the time). This ADR computes the real
breakdown, at zero additional quota cost — the held-out corpus's 106 cassettes were already
recorded (ADR 0021); this is pure analysis of already-computed scoring output
(`eval/results/held_out_scoring_v2.json`), via a new script,
`eval/analyze_coverage_metrics.py`.

## Method

Session 6/9's existing hedge vocabulary (`docs/specs/wave1-coverage-abstention-analysis.md`,
`HEDGE_FIELDS` convention from `eval/score_held_out_corpus.py`): the two fields this product's
schema allows to hedge are `sentiment` (hedge value `"mixed"`) and `buy_again` (hedge value
`null`). For each:

- **coverage**: fraction of the 106 fixtures where the model committed to a non-hedge answer.
- **accuracy_on_answered**: mean field score among only the committed answers.
- **wrong_committed**: among committed answers, the count that scored exactly 0 — a full miss,
  not a partial fuzzy-credit mismatch. Verified this is unambiguous for both fields before
  computing anything: checked all 106 held-out fixtures' `scoring_notes.exact_match_fields` and
  confirmed `sentiment`/`buy_again` are exact-match scored (0.0 or 1.0 only, never a fractional
  fuzzy score) in every single one — `wrong_committed` is exactly "committed and got it wrong,"
  with no ambiguity from partial-credit scoring blurring the count.

All figures use the `as_deployed` condition (real language routing, ADR 0021) — the number that
reflects what a customer actually experiences, not the routing-corrected number. 95% CIs are
percentile bootstrap (`eval/bootstrap.py`, seed=42, 10,000 resamples, same mechanism used
throughout this project).

## Results (n=106)

| Field | Coverage | Accuracy-on-answered | Wrong-committed |
|---|---|---|---|
| sentiment | 77.4% [68.9%, 84.9%] | 87.8% [80.5%, 93.9%] | 10/82 = 12.2% [6.1%, 19.5%] |
| buy_again | 43.4% [34.0%, 52.8%] | 76.1% [63.0%, 87.0%] | 11/46 = 23.9% [13.0%, 37.0%] |

## P3b — comparison to the n=23 figures, and a discrepancy that needs flagging

The brief cited "buy_again 0/10 wrong-committed, sentiment 1/12" at n=23 as the prior figures to
compare against. **These exact counts could not be reconciled against this repo's own published
n=23 numbers** (ADR 0018): that ADR reports buy_again coverage 43.5% (→ ~10 of 23 answered,
consistent with "10" in "0/10") and accuracy-on-answered 70.0% — but 70% of 10 answered implies
7 correct / **3 wrong**, not 0. Similarly for sentiment, ADR 0018 reports coverage 82.6% (→ ~19
answered, not 12) and accuracy-on-answered 89.5% (→ ~17 correct / **2 wrong**, not 1-of-12's
implied denominator). The counts and the denominators don't line up with any published source
this session could find. **Reported plainly rather than silently adopted or silently
recomputed to match**: this may reference a different session's analysis (possibly the original
49-fixture CI-gate set, not the held-out corpus) that this session did not locate. GG: if you
have the source for "0/10"/"1/12," point to it and this comparison can be redone against the
actual matching subset.

**What can be honestly compared**: ADR 0018's own n=23 accuracy-on-answered figures (buy_again
70.0% [40%,100%], sentiment 89.5% [73.7%,100%]) against this session's n=106 figures (buy_again
76.1% [63.0%,87.0%], sentiment 87.8% [80.5%,93.9%]). Both point estimates are statistically
indistinguishable from the n=23 figures (all four CIs overlap substantially) — **the wide n=23
CIs were never precise enough to have detected the difference this ADR is now reporting**. The
value of n=106 here isn't that the number moved; it's that the CI is now tight enough to say
something with confidence: buy_again's wrong-committed rate excludes 0 with reasonable
confidence (13.0% lower bound), which n=23's own data could never have established either way.

## P3c — the headline claim, stated with its CI, not outrunning the evidence

**"Rarely wrong when it commits" does NOT hold as a single claim across both fields, and should
not be published as one.** The honest single-sentence claim: *when this model commits to an
answer, it is correct 87.8% of the time for sentiment [80.5%, 93.9%] but only 76.1% of the time
for buy_again [63.0%, 87.0%] — a materially higher error rate for buy_again that a single
"rarely wrong when it commits" headline would misrepresent.* If the landing page or outreach
email needs one number, it must be per-field, not blended, and buy_again's real committed-error
rate (roughly 1 in 4) is the number that determines whether "rarely wrong" is honest to publish
at all — it is not, for that field.

## Decision

- Publish both fields' full breakdown (coverage, accuracy-on-answered, wrong-committed, all with
  CIs) via metrics-injection, not a single blended claim (P3d, separate PR).
- Do NOT publish "rarely wrong when it commits" as a standalone claim anywhere customer-facing.
  If a committed-accuracy claim is wanted for marketing, it must name the field and its CI
  explicitly (e.g. "87.8% accurate when it commits to a sentiment call") — never generalized
  across fields with materially different rates.
- The n=23→n=106 discrepancy in wrong-committed counts is not itself alarming (CIs overlap), but
  it is a concrete demonstration of why n=23 was never enough to make a committed-accuracy claim
  — this is worth keeping in mind for any future metric computed on a small held-out slice.

## Alternatives considered

- **Silently substitute the correctly-reconciled n=23 numbers for GG's cited ones and proceed as
  if nothing were amiss.** Rejected — the discrepancy might indicate GG has a different, real
  source this session hasn't found, and silently overwriting it would hide that possibility
  rather than surface it for GG to resolve.
- **Report a single blended "committed accuracy" number across both fields.** Rejected: the two
  fields diverge by more than 11 percentage points with non-overlapping practical implications
  (buy_again's ~1-in-4 committed-wrong rate is a materially different customer-facing risk than
  sentiment's ~1-in-8) — blending them would itself be the "flat accuracy" framing problem this
  entire exercise exists to move past.
