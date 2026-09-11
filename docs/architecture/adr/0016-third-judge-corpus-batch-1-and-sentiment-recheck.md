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

## Follow-up (Session 10 P2) — the retraction above was wrong; Gemini is a degenerate hedger

The retraction above treated Gemini's 9/9 hedge as legitimate independent judgment on
genuinely ambiguous text. That was never tested against the alternative: Gemini hedging
*specifically because the item is hard*, independent of whether it's actually ambiguous —
the same failure class as `allam-2-7b` (dropped from the panel for a different reason,
9/33 calibration misses) and the all-NO voter that collapsed a defect-detection ensemble in
an earlier engagement. Calibration cannot distinguish these: a 16-item unambiguous-by-
construction control set cannot detect a judge that only fails on hard items, because it
contains none.

**Test**: built a hard-but-decidable control from already-labeled fixtures — zero new
sourcing, per the task's own constraint. From `eval/results.json`'s per-fixture scores +
`eval/consensus/results/consensus_labels.jsonl`'s cached votes, selected every fixture
where (a) `qwen/qwen3.6-27b` matches the fixture's own ground-truth sentiment exactly, and
(b) the production model's own prediction does NOT match ground truth (wrong or hedged) —
i.e., items empirically hard enough to trip the production model, but where a disjoint
judge already agrees with gold on the decisive answer. 9 candidates matched; 4 were
themselves gold-labeled `mixed` (uninformative for hedge detection — a `mixed` answer on a
genuinely-mixed item is correctness, not evidence of hedging) and excluded, leaving 5 with
a decisive (non-`mixed`) gold answer: `012_sarcasm` (negative), `015_medium_urgency`
(negative), `016_no_product_name` (neutral), `022_two_star_explicit` (negative),
`025_competitor_switch` (positive).

**Result — Gemini hedged on 4/5 (80%)**, fresh calls against the exact review texts, not
inferred: `012`→mixed, `015`→mixed, `016`→neutral (correct), `022`→mixed, `025`→mixed. The
one item it got right (`016`) is also the least emotionally-loaded of the five (a flatly
unremarkable review: "Works as advertised I guess... nothing special but nothing broken
either") — consistent with Gemini committing fine on genuinely low-signal text and
defaulting to `mixed` specifically when the text carries real directional content (sarcasm,
frustration, an explicit competitor comparison) that a disjoint judge and the original
label both resolve cleanly.

**Cross-check: `qwen/qwen3.8-27b` (same vendor as qwen3.6, different checkpoint) run fresh
against the same 5 items — matched gold on 4/5**, hedging on only `015_medium_urgency`
(the one item qwen3.6 and qwen3.8 disagree on). This is genuine, if same-vendor-weaker,
independent corroboration of qwen3.6's original finding that did not exist when ADR 0012's
bar was written — two different Qwen checkpoints agreeing on 4 of 5 hard cases is
meaningfully more than the single-rater status quo, even though it doesn't meet the
"cross-vendor disjoint" bar as originally specified.

**VERIFIED: reading (ii) holds for Gemini.** It is a degenerate hedger on hard-but-decidable
sentiment cases specifically — 0/33 misses on unambiguous calibration items, 4/5 hedges on
items a disjoint judge and gold both resolve decisively. This is not evidence the calibration
was faked or gamed; it's evidence that **calibration on an unambiguous-by-construction set
cannot detect a judge that fails specifically on hard items** — a generalizable point, not
specific to this judge or this field, worth carrying into any future judge-vetting: a control
set needs a hard-but-decidable arm, not just an easy arm, to catch this failure mode at all.

**Consequence: the Session 9 retraction above is itself retracted.** Gemini's 9/9 hedge does
not contradict qwen3.6's 4/9-decidable finding — it reflects a hedging bias that calibration
alone couldn't surface, not independent judgment on genuine ambiguity. This restores ADR
0013's finding to standing, un-contradicted evidence. **It does not, by itself, authorize a
sentiment experiment** — ADR 0012's bar asks for a second genuinely disjoint judge, and
Gemini (now shown unreliable specifically on hard sentiment calls) cannot fill that role.
qwen3.8's 4/5 corroboration is real but same-vendor, the exact "weaker evidence" class ADR
0015 already flagged when it was added. Whether that same-vendor corroboration is *enough*
to authorize the experiment is a policy call for GG, not a decision this session makes
unilaterally — reported with the evidence in hand, not decided. **`sentiment` stays
unauthorized for prompt-level experimentation either way** until that call is made; nothing
here changes P6's prompt freeze.

Practical implication for the panel going forward, independent of the authorization
question: Gemini's sentiment vote should be weighted with this caveat on any genuinely
contested (near-tie) item — exactly the items where a 3rd judge's tiebreaking vote matters
most in a 3-way majority. This does not contradict Session 9's batch-1 finding (high
Gemini/qwen agreement, alpha ~0.94–1.0, on 23 unselected real hi-en reviews) — most real
reviews are not hard in this specific sense; the failure surfaces on the hard tail
specifically, which an unselected batch under-samples relative to a control set built to
target it.

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
5. (Session 9) `sentiment` stays unauthorized for prompt-level intervention -- **superseded by
   Session 10 P2 below**: Gemini's contradiction is retracted, but authorization still isn't
   granted; the reason moves from "second judge disagrees" back to "no fully cross-vendor
   disjoint second judge exists yet", pending GG's call on whether qwen3.6+qwen3.8's same-vendor
   corroboration clears ADR 0012's bar.

## Consequences

- The held-out corpus's real, achievable ceiling is now 106 hi-en + 0 hi, not 106 + 2 -- P3d's
  "prioritize Hindi" instruction is honored in ordering, but there is currently no genuine
  Devanagari-script product-review content in this specific public corpus to prioritize. This
  does not change the main (non-quarantined) `eval/fixtures/hi/`'s existing 6 Hindi fixtures,
  which come from a different source and are untouched by this finding.
- 83 hi-en candidates remain for future batches under the same P3c ceiling discipline.
- `sentiment`'s prompt-freeze (P6) continues indefinitely pending GG's decision on whether
  qwen3.6+qwen3.8's same-vendor 4/5 corroboration (Session 10 P2) meets ADR 0012's bar, or
  whether a genuinely cross-vendor non-degenerate second judge is still required -- that
  decision is GG's, not this session's, per the standing STOP-and-report discipline for
  anything touching `app/core/prompts/**`.
- Any future judge-vetting for this project should include a hard-but-decidable calibration
  arm, not just an unambiguous one -- Session 10 P2's central finding is that the current
  calibration design structurally cannot catch a judge that hedges specifically on hard items.

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
