# ADR 0019: Held-out corpus complete (106/106 hi-en) — the language boundary is genuinely hard

## Context

Session 9 built the quarantined held-out corpus and labeled 23 of 108 candidates (2 later
found to be false positives, ADR 0016 — real ceiling 106 hi-en, 0 hi). Session 10 P5e finishes
it, once ADR 0015's corrected rate-limit understanding (this session, same ADR) confirmed the
remaining 83 candidates fit comfortably inside a single day's revised ceiling.

## What happened

Labeled the remaining 83 hi-en candidates in two batches (13, then 70 — split only by a
mid-run stop to diagnose and fix the OTPM issue documented in ADR 0015's correction, not by any
real ceiling). Both batches, post-fix: 0 errors, 0 items split on the growth-gate fields.
**The held-out corpus is now complete: 106/106 hi-en candidates labeled, 0 hi (confirmed
genuinely zero in this corpus, ADR 0016/0017).**

## Full-corpus reliability (106 items, all three judges, excluding the 2 discarded false
positives)

| Field | 3-judge alpha | qwen3.6+qwen3.8 | qwen3.6+Gemini | qwen3.8+Gemini | kappa |
|---|---|---|---|---|---|
| sentiment | 0.942 | 0.930 | 0.947 | 0.948 | 0.941 |
| buy_again | 1.000 | 1.000 | 1.000 | 1.000 | 0.647 |
| urgency (ordinal) | 0.796 | — | — | — | — |
| **language** | **0.380** | 0.423 | 0.332 | 0.391 | 0.379 |

Sentiment and buy_again replicate Session 9's batch-1 finding at 4-5x the sample: high
agreement, no vendor-clustering pattern (all three pairs land within a few points of each
other on sentiment; alpha=1.0 for buy_again across every pair).

**`language` is the exception, and it isn't a vendor-clustering artifact either — it's low
for every pair, symmetrically (0.33–0.42, no pair meaningfully higher than another).** The
panel itself doesn't agree on the en/hi-en boundary. This directly corroborates ADR 0018's
separate finding (production's own detector disagreed with the corpus's language tag on 11/23,
48% of held-out fixtures) — the two findings were produced by completely independent methods
(panel inter-rater agreement vs. production-vs-corpus-label accuracy) and land on the same
conclusion from different directions: **classifying "how much code-mixing makes a review
hi-en rather than en" is a genuinely hard, low-agreement task for every method tried so far —
three LLM judges, a corpus-mining heuristic, and a production detector — not a competence gap
specific to any one of them.**

## Decision

- Held-out corpus growth is complete for this corpus (`eval/data/flipkart_candidates.jsonl`'s
  hi-en yield is fully exhausted at 106; growing further needs a new source, out of scope
  here).
- `eval/consensus/panel.py`'s `max_completion_tokens` fix (900, ADR 0015) ships in this PR —
  required for this batch to have completed at all, and load-bearing for any future labeling
  run against this panel.
- **No fix proposed here for the language-boundary finding.** Unlike ADR 0018's production
  danda bug (an unambiguous false positive with a clear fix), low inter-rater agreement on a
  genuinely fuzzy linguistic boundary isn't a bug with a code fix — it's evidence the en/hi-en
  distinction, as currently specified (a binary label), may need a more precise operational
  definition (e.g., a code-mixing density threshold stated explicitly, not left to each
  detector/judge's own implicit heuristic) before further work assumes the boundary is
  well-defined. Recommend, don't decide: this is a definitional/product question for GG, not
  an engineering one.

## Consequences

- The corpus now supports real P4a/P4b-style scoring at full power (n=106, not n=23) —
  ADR 0018's contamination-gap finding (12.7pp, n=23) can be re-measured at n=106 in a future
  session for a materially tighter CI, without any new labeling cost (scoring is a separate,
  zero-panel-quota step against fixtures that already exist).
- Any future prompt or detector work touching the en/hi-en boundary should treat "language"
  disagreement as expected background noise at this rate (~40-46% pairwise disagreement),
  not as a signal that one specific component is unusually broken.
- `eval/consensus/results/held_out_batch_log.jsonl` now contains entries from three
  discontinuous runs (Session 9's batch 1, this session's aborted OTPM-broken attempt, and
  this session's clean completion) — append-only by design (Session 9's stated convention),
  kept as the honest audit trail rather than cleaned up, per that same convention.

## Alternatives considered

- **Treat `language`'s low alpha as disqualifying and drop it from the panel's scored
  fields.** Rejected: the disagreement is informative (it's telling us the boundary is fuzzy),
  not noise to be hidden — dropping the field would remove exactly the signal that led to
  ADR 0018's production finding being corroborated independently.
- **Propose a specific code-mixing density threshold now, to resolve the boundary.**
  Rejected: this session found the problem (three independent methods agree it's hard) but
  has no principled basis yet for where the "right" threshold sits — proposing one without
  evidence would repeat the exact mistake ADR 0014 corrected (a threshold chosen without
  sampling).
