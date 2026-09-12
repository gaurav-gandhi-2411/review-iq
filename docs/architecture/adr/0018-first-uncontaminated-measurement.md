# ADR 0018: The first uncontaminated measurement — 67.9%, a 12.7pp contamination gap

## Context

Every number in `eval/results.json` before this ADR comes from fixtures the prompt was
developed and iterated against, directly or by construction (`eval/README.md`'s own D3
discussion). Session 8 built a quarantined held-out corpus specifically to give this product
its first measurement against text no prompt author ever saw; Session 9 grew it to 23 real
hi-en fixtures (LLM-consensus silver, never human-verified — P3f). This ADR reports the first
actual scoring run against it.

## Method

Scored the LIVE deployed demo endpoint (`POST /demo/extract`) against all 23 quarantined
fixtures, not an in-process call — `demo.py` and `app/api/v2/extract.py` both call the
identical `extract_with_llm()` (same tiered router, same prompts), so this measures exactly
what a real customer's `/v2/extract` call would produce today, not this checkout's local
state. Paced at 15s/call (well under the endpoint's real 5/minute limit). Scored with
`eval/runner.py`'s own `score_fixture()` — the identical scoring logic behind every other
published number, so this is comparable, not a new methodology. New script:
`eval/score_held_out_corpus.py`.

**Correction during the run**: the first pass (25 items, unpaced beyond 3s) hit 9 errors — 5
from a self-inflicted mistake (zeroing the demo cap for an unrelated batch job while this
scoring run was still in flight, briefly 429-ing it) and 4 from underestimating the real
5/minute limit. Retried all 9 at 15s pacing; all succeeded on retry, 0 final errors. Disclosed
because it happened, not because it changed the result.

## Result

**Overall: 67.9%, 95% CI [63.6%, 72.3%], n=23.** Lower than every published number, as
expected (P3b) — reported as measured, nothing tuned in response.

**Contamination gap vs. the currently-published hi-en number (80.6%, n=15): 12.7 percentage
points, 95% CI [5.9%, 19.2%]** (two-sample percentile bootstrap on the two independent
fixture-score samples). The interval excludes zero — this is a real, measurable effect, not
noise from small n. This is the first time this product has had a number for "how much is the
published hi-en score inflated by fixtures the prompt was tuned against" — 12.7pp is that
number, today, for this fixture set. (A parallel PR, #154, corrects a mislabeled fixture that
moves the *published* hi-en figure to 75.6%/n=16 — not yet merged as of this measurement; the
comparison above uses whichever is currently live on `main`. Once #154 merges, the gap becomes
~7.7pp against the corrected number — smaller, but from fixing a labeling bug, not from any
real change in what production does.)

### Per-field breakdown

| Field | Accuracy | Coverage | Accuracy-on-answered |
|---|---|---|---|
| sentiment | 91.3% | 82.6% | 89.5% [73.7%, 100%] |
| buy_again | 65.2% | 43.5% | 70.0% [40%, 100%] |
| stars / stars_inferred | 100% | — | — |
| competitor_mentions | 82.6% | — | — |
| pros | 69.0% | — | — |
| cons | 58.4% | — | — |
| topics | 51.9% | — | — |
| **language** | **52.2%** | — | — |
| **product** | **8.7%** | — | — |

**Two findings need a caveat before being read as capability gaps:**

- **`product` (8.7%) is a scoring artifact, not a capability failure.** Inspection of every
  mismatch shows near-miss paraphrases scored as hard failures by exact-match: predicted
  `"unknown product"` vs. expected `"headphones"`, `"audio device"` vs. `"unknown product"`,
  `"earbud"` vs. `"earbuds"` (singular/plural). Both sides are reasonable, differently-worded
  answers for reviews that often don't name a specific product. This number should not be read
  as "the model gets the product wrong 91% of the time" — it reads that way only under strict
  string equality, which is the wrong scorer for this field's actual variability. Not fixed
  here (scoring methodology changes are their own decision); flagged so it isn't
  misinterpreted or published as a capability claim.

- **`language` (52.2%) is real, and traces to a live production bug just found and fixed on a
  separate PR (#156).** 11/23 fixtures the corpus tags `hi-en` were classified `en` by
  production. Root cause: `app/core/language.py`'s Devanagari detector had the exact same
  danda-punctuation false positive ADR 0016 found and fixed in the corpus-mining script — a
  live sibling that had never been checked. Fixed on #156 (not self-merged: merging auto-
  deploys per `.github/workflows/deploy-cloud-run.yml`, an explicit escalation trigger). This
  fix alone does not close the full 52.2%→100% gap — most of the 11 mismatches are borderline
  reviews with light code-mixing (1-2 weak Hinglish markers), and separately, `app/core/
  language.py`'s weak-marker threshold (`>= 3`) was never updated to match ADR 0014's corpus-
  detector fix (`WEAK_HINGLISH_THRESHOLD = 1`, justified there by direct sampling for the
  corpus-mining use case). **Not fixed here** — unlike the danda bug, this is a genuine
  precision/recall tradeoff a corpus-mining threshold choice doesn't automatically transfer to
  production routing, and needs its own decision, not an assumed port. Recommend: sample
  production's own borderline-Hinglish false-negative rate at threshold 3 vs. 1 before
  changing it, the same evidence-first approach ADR 0014 used.

`sentiment` and `buy_again` show real, if partial, hedging (43.5%/82.6% coverage) — consistent
with this product's known hedging pattern (`docs/specs/wave1-coverage-abstention-analysis.md`,
ADR 0012/0013/0016), now confirmed on genuinely unseen text, not just the CI-gate set.

## Decision

- Published as **provisional, n=23, LLM-consensus silver** — not human ground truth, not
  published to any public surface (README/site/`.portfolio/metrics.json`) yet, per P3d.
  `eval/results/held_out_scoring_latest.json` is the artifact; nothing in this PR touches
  `eval/results.json`, `eval/results/latest.json`, or any metrics-injection target.
- `product` field's exact-match scoring flagged as a methodology gap, not fixed (scoring
  logic changes are their own decision, out of scope for a measurement PR).
- `language` field's production/corpus threshold divergence flagged, not fixed — the danda
  half is fixed separately (#156); the threshold half needs its own evidence-gathering,
  recommended above, before any change.

## Consequences

- This is the first real, disclosed evidence of how much fixture contamination has been
  inflating this product's own accuracy claims — 12.7pp, CI excludes zero. Future eval-gate
  threshold or public-claim discussions should account for this, not treat the CI-gate number
  as the product's real-world accuracy.
- The held-out corpus's continued growth (83 hi-en candidates remaining, P5e) matters more
  after this finding, not less — n=23 is enough to detect a large gap, not enough to
  characterize it precisely (the gap's own CI is [5.9pp, 19.2pp], a wide range).
- Two live findings (the danda-detector sibling bug, the threshold divergence) exist only
  because this measurement was run against real, unseen text — a second, independent argument
  (beyond the accuracy-inflation one) for why contaminated-only measurement was hiding real
  product bugs, not just overstating a percentage.

## Alternatives considered

- **Fix `product`'s scoring to use a looser match (e.g., set overlap on category tokens)
  before publishing this number.** Rejected for this PR: changing scoring methodology and
  reporting a first uncontaminated measurement are two different decisions: bundling them
  would make it unclear whether the reported number reflects the corpus finding or the
  scorer change. Reported with the caveat instead; a scoring fix is a legitimate follow-up.
- **Port ADR 0014's threshold=1 to production immediately, since it's "the same fix."**
  Rejected: the corpus-mining use case explicitly optimizes for recall (catch every possible
  Hinglish candidate, verified acceptable via manual sampling of false positives) — production
  routing has a different cost structure (misrouting genuinely-English text to the Hinglish
  prompt path has a real, unmeasured quality cost the corpus use case doesn't share). Treating
  them as interchangeable would be exactly the "assumed instead of measured" mistake ADR 0014
  itself corrected on the corpus side.
