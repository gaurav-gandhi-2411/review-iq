# ADR 0016: Third judge sourced, held-out corpus batch 1, and a sentiment finding retracted

## Context

Session 9 P1 unblocked the demo global cap (#151, merged and verified live) that ADR 0015
identified as the missing prerequisite for running any real labeling batch without exposing
production customer traffic to an unbounded shared Groq quota draw. This unblocks the two
things ADR 0015 left pending: sourcing a genuinely cross-vendor third judge (P3a), and running
the held-out Hindi/Hinglish corpus's first real labeling batch (P3c).

## Third judge: gemini-3.5-flash-lite

Two real dead ends before a working model, each root-caused from a raw exception, not assumed:

- `gemini-2.5-flash`: calibration failed at 9/33 misses, concentrated in later items -- traced
  to a **20-requests-per-day** free-tier cap specific to that model
  (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`), not the 5/minute limit an earlier
  retry-with-backoff fix already handled.
- `gemini-2.5-flash-lite`: calibration failed at 33/33 misses (every field, every item) --
  traced to an HTTP 404: the model is fully deprecated for new users, and the API's own error
  message names `gemini-3.5-flash-lite` as the replacement.
- `gemini-3.5-flash-lite`: calibrated clean, 0/33 misses (`eval/consensus/results/
  calibration_report.json`). Self-judging check done manually (Gemini isn't a Groq model, so
  `assert_no_self_judging()` doesn't cover it): confirmed live via `gcloud run services
  describe` that production's `ENABLE_GEMINI_FALLBACK` is unset (code default `False`) --
  Gemini's SecondaryProvider path is genuinely dormant, not just low-traffic. Point-in-time
  fact, re-check if this ever changes.

Active panel is now 3 judges: `qwen/qwen3.6-27b`, `qwen/qwen3.8-27b`, `gemini-3.5-flash-lite`.
`allam-2-7b` stays dropped (9/33 misses, reproducible across two independent runs).

An ambient, unrelated `GOOGLE_API_KEY` shell variable was found to silently override an
explicitly-passed `GEMINI_API_KEY` in the `google-genai` SDK (it prints which one it chose,
easy to miss), producing one false-negative calibration run (16/33 misses) before being
diagnosed and fixed by explicitly clearing `GOOGLE_API_KEY=` alongside the real key in every
invocation. `eval/consensus/panel.py` reads `GEMINI_API_KEY` directly from `os.environ`, no
dotenv loading -- callers must export it explicitly.

## A second, real corpus-quality bug found while running batch 1

Both of the held-out corpus's only 2 "hi" (Devanagari) candidates turned out to be pure English
text using a stray Devanagari danda (।, U+0964) as a period/separator -- discovered when the
panel's own judges labeled both `language=en` against their source "hi" tag, not asserted by
inspection first. Root cause: `eval/data/sample_flipkart.py`'s `_DEVANAGARI` regex matched the
whole Devanagari Unicode block, which includes punctuation (danda, double danda) that carries
no language signal. Fixed by excluding `U+0964`-`U+0965` from the match
(`eval/data/sample_flipkart.py`); reclassified the existing `flipkart_candidates.jsonl` locally
(no new downloads) -- exactly those 2 rows flipped to `en`, zero other rows changed. **The
corrected corpus has zero genuine Hindi (Devanagari-script) candidates, not 2** -- a starker
version of ADR 0014's "108 is the real ceiling" finding: it's 106 (hi-en only). The 2
mislabeled fixtures were deleted from the quarantine directory rather than kept under a
misleading `hi-` id.

## Batch 1: 23 held-out hi-en fixtures written

P3c protocol followed: production's `DEMO_DAILY_REQUEST_BUDGET` set to `0` before the batch
(confirmed via a live `curl` against `/demo/extract` returning 429 with the quota-exhausted
message), restored via `--remove-env-vars` after (confirmed via a live 200 with a real
extraction). Batch size (25 items, later corrected to 23 usable) chosen against ADR 0015's
documented 200,000-tokens/day, 1,000-requests/day organizational ceiling: 50% = 100,000 tokens,
and 25 items x 2 Groq judges x ~1,934 tokens/call (ADR 0015's measured hi-en rate) ~= 96,700
tokens, under the ceiling. Actual spend: 50 confirmed Groq requests (25 items x 2 Groq judges);
token figure is the estimate above, not separately logged this run -- flagged as an estimate,
not a measurement, pending a future run that captures `usage` directly.

**Open question, not resolved here**: a live Groq rate-limit header probe this session
(`x-ratelimit-limit-tokens: 8000`, reset ~134ms; `x-ratelimit-limit-requests: 1000`, reset
~1m26s) shows a rolling per-minute-scale budget, not obviously the same shape as ADR 0015's
200K/day figure sourced from the Groq console UI. Both could be true simultaneously (a burst
TPM/RPM layered under a stricter daily account cap), but this session did not reconcile them.
Treated the more conservative, GG-documented daily figure as the operative ceiling for P3c
compliance; flagging the discrepancy rather than quietly picking whichever number was more
convenient.

All 23 items reached full growth-gate consensus (unanimous or majority on
sentiment/urgency/buy_again/language) -- 0 split. Written to
`eval/fixtures/_held_out_hindi_hinglish/hien-0001.json` through `hien-0023.json`. 83 hi-en
candidates remain for future batches; 0 hi candidates remain (see above).

### Real inter-rater reliability, computed from these 23 items' raw votes

Unlike the calibration control set (unambiguous by construction -- every passing judge gets
every field right, so kappa/alpha there is trivially 1.0 and tells us nothing about real
disagreement), these 23 items are genuine unseen reviews with real judgment calls. Computed via
`eval/agreement.py`'s existing `krippendorff_alpha`/`fleiss_kappa` against the raw per-judge
votes in `eval/consensus/results/held_out_batch_log.jsonl`:

| Field | 3-judge alpha | qwen3.6+qwen3.8 | qwen3.6+gemini | qwen3.8+gemini |
|---|---|---|---|---|
| sentiment | 0.957 (kappa 0.956) | alpha 0.936 | **alpha 1.0** | alpha 0.936 |
| buy_again | 1.0 (kappa 0.779) | kappa 0.866 | kappa 0.672 | kappa 0.800 |
| language | 1.0 (kappa 1.0) | 1.0 | 1.0 | 1.0 |
| urgency (ordinal) | 0.936 | 1.0 | 0.903 | 0.903 |

**P3a's named concern -- do the two qwens cluster together and exclude Gemini? No.** On
sentiment, Gemini agrees with qwen3.6 *more* (alpha 1.0) than the two qwens agree with each
other (0.936). On urgency, the qwen pair agrees more with each other, but Gemini's agreement
with either qwen (0.903) isn't dramatically lower. `buy_again`'s kappa numbers vary by pair
(0.67-0.87) despite alpha reading 1.0 for every pair -- both are read directly from this
repo's existing, pre-built `eval/agreement.py`/`build_report.py`, not re-derived here; the
alpha/kappa divergence on this one field is noted but not chased further, since it doesn't
change the qualitative finding (no vendor-clustering pattern) either way. **No evidence in this
batch that the qwen pair's shared vendor is inflating apparent agreement.**

## A prior conclusion retracted: sentiment's second disjoint judge does NOT validate the finding

ADR 0012/0013 named a specific, falsifiable prerequisite for authorizing a sentiment prompt
experiment: "a second genuinely disjoint, calibration-passing judge validates the finding" that
`qwen/qwen3.6-27b`, alone, found 4/9 real sentiment hedges decidable (matching the fixtures'
original ground truth exactly on all 4: `012_sarcasm`, `015_medium_urgency`,
`022_two_star_explicit`, `025_competitor_switch`). Session 9 P3a's Gemini judge was the
candidate for that role -- so it was actually run against those exact 9 fixture texts (fresh
calls, not inferred), rather than assumed to pass because it calibrated well elsewhere.

**Result: Gemini answered `sentiment: mixed` on all 9/9 cases, including the 4 qwen3.6 resolved
decisively.** This does not validate qwen3.6's finding -- it contradicts it. Gemini's hedge
pattern matches the ORIGINAL model-under-test and the (excluded, contaminated)
`openai/gpt-oss-120b` judge's pattern from ADR 0013, not qwen3.6's more decisive reads.

This is not evidence Gemini is a bad judge (calibration was clean, 0/33, and it agreed strongly
with qwen3.6 on the broader held-out batch above) -- these 9 items were hand-selected
specifically as real-hedge edge cases, and on inspection several read as genuinely
mixed-with-a-caveat (e.g. `027_harm_in_positive_tone_high`: "Really loving these
headphones!... Only issue is the ear cups press really tight, my ears start aching... Still a
great buy"). The honest reading is that this is a real, substantive disagreement between two
disjoint judges on a small (n=9), deliberately-ambiguous item set -- not a contamination
artifact, and not resolved by picking the rater whose answer is more convenient.

**Per ADR 0012's own pre-registered bar, this means the bar is NOT met.** `sentiment` stays
EXCLUDED from prompt-level experiment authorization -- not because of insufficient evidence (as
ADR 0013 left it), but because the second disjoint judge actively disagrees with the first.
This is a stronger reason for caution than before, not a weaker one, and it retracts the
implicit optimism in ADR 0013's framing that a second rater would likely confirm the finding.
`buy_again`'s authorization (ADR 0012, 9/10 decidable per the single disjoint judge) is
unaffected -- untouched by this check, no new evidence against it.

## Decision

1. Panel restored to 3 calibration-passing judges (`eval/consensus/panel.py`,
   `calibration_report.json`).
2. `eval/data/sample_flipkart.py`'s Devanagari detector fixed; `flipkart_candidates.jsonl`
   regenerated (locally, zero new downloads/quota).
3. `eval/consensus/build_held_out_corpus.py` (new): batched, resumable, Hindi-first (P3d)
   labeling script for the quarantined corpus only -- distinct from `run_consensus.py`, which
   grows the main, prompt-visible fixture set and must never be pointed at the quarantine
   directory or vice versa.
4. Held-out corpus grown from 0 to 23 real hi-en fixtures (0 hi -- see above).
5. `sentiment` stays unauthorized for prompt-level intervention (ADR 0012's bar not met, for a
   new and stronger reason than previously documented).

## Consequences

- The held-out corpus's real, achievable ceiling is now 106 hi-en + 0 hi, not 106 + 2 -- P3d's
  "prioritize Hindi" instruction is honored in ordering, but there is currently no genuine
  Devanagari-script product-review content in this specific public corpus to prioritize. This
  does not change the main (non-quarantined) `eval/fixtures/hi/`'s existing 6 Hindi fixtures,
  which come from a different source and are untouched by this finding.
- 83 hi-en candidates remain for future batches under the same P3c ceiling discipline.
- `sentiment`'s prompt-freeze (P6) continues indefinitely pending either new evidence or a
  design decision to proceed despite disjoint-judge disagreement -- that decision is GG's, not
  this session's, per the standing STOP-and-report discipline for anything touching
  `app/core/prompts/**`.

## Alternatives considered

- **Treat Gemini's calibration pass as sufficient and skip the targeted 9-item re-check.**
  Rejected: ADR 0012's bar is specifically about validating *this* finding on *this* fixture
  set, not general judge competence -- calibration measures the latter, not the former, and
  conflating them would have let a subtler, more damaging error (declaring `sentiment`
  authorized on the strength of an unrelated calibration pass) stand unexamined.
- **Discard Gemini's 9-item result as noise, since it disagrees with the already-accepted
  qwen3.6 finding.** Rejected: nothing about qwen3.6's read is privileged over Gemini's here --
  both are calibration-passing, disjoint judges: preferring one because it was first, or
  because its answer was more useful to the point the last two sessions were building toward,
  is exactly the kind of motivated reasoning the standing "push back with evidence" instruction
  exists to prevent.
