# Project Spec: review-iq — Bounded Prompt-Level Model Improvement (Task #2)

## Goal
A BOUNDED attempt to improve two known weak spots via prompt-level changes only: (1) hi-en
(Hinglish) extraction accuracy — the differentiator and lowest score (~80%); (2) urgency
harm-in-positive-tone detection — the en-013-style miss (harm signal buried in a positive review),
still only half-solved. Bounded = 1-2 iterations per target, measured honestly, revert if not
clearly better. No fine-tuning (that's data-gated, deferred). Free tier, $0.

## Honest measurement caveats (define success correctly)
- The benchmark's hi/hi-en gold labels are LLM-GENERATED (internal tool, not published-credible).
  So "hi-en accuracy up" = "agreement with LLM labels up" — partly signal, partly noise. A small
  delta (<~3pp) is WITHIN NOISE and must NOT be treated as real improvement.
- Therefore the GATE is dual: (a) the metric improves meaningfully AND (b) a human spot-check of
  the CHANGED cases shows genuinely better extractions by GG's judgment. The number is a hint; the
  human read is the real gate. Neither alone is sufficient.
- Urgency has a HUMAN-ADJUDICATED rubric (better ground truth than hi-en) — that target is more
  trustworthy to measure. Weight its metric more; still spot-check.
- NEVER tune toward the LLM labeler's opinions (the adjudication proved it has a medium-bias and
  was wrong on harm cases). Tune toward the RUBRIC / GG's judgment of correct output.

## Current state
- Extraction prompt v2.2 in prod. Eval: en ~86 / hi ~81 / hi-en ~80 / overall ~83.5 (agreement
  with the internal benchmark labels). Cassette-replay eval CI (re-record when prompt changes —
  a prompt change invalidates cassette keys).
- Urgency rubric (adjudicated, authoritative): HIGH = harm/safety signal (regardless of star
  rating/positive tone) OR explicit refund/return demand OR systemic/batch defect; MEDIUM =
  fixable functional defect, no harm/escalation; LOW = no actionable defect. Known residual:
  harm-in-positive-tone (en-013 "eyes will start paining" in a 5-star review) classified low/medium,
  should be HIGH — positive framing masks the harm signal.

## Scope

### In scope
**Target A — hi-en extraction quality (bounded):**
- 1-2 prompt iterations targeting Hinglish/code-mixed handling (the failure patterns in the hi-en
  eval slice — inspect the actual mislabeled cases first, tune to THOSE, don't guess).
- Re-record cassettes for the changed prompt (all fixtures, one clean pass — a prompt change
  invalidates old cassette keys; partial = stale/mixed, forbidden). Re-run the benchmark.
- GATE: metric up >~3pp (above noise) AND GG spot-checks the changed hi-en cases and confirms
  genuinely better. If not clearly better → REVERT, log "no bounded gain," move on.

**Target B — urgency harm-in-positive-tone (bounded, better-measured):**
- Prompt change so physical-harm/safety signals classify HIGH regardless of positive tone or star
  rating (the en-013 pattern). Keep existing correct behavior (don't regress the already-correct
  high-calls or over-flag).
- Add regression fixtures: harm-in-positive-tone→high, positive-no-harm→low, existing high-calls
  stay high, defect-no-escalation→medium. These PIN the behavior permanently.
- Re-record cassettes, re-run. GATE (rubric is adjudicated, so more trustworthy): the harm-in-
  positive cases now classify HIGH, no regression on existing correct calls (a test proves both).

### Out of scope
- Fine-tuning / any model training (data-gated, deferred — Task #3, not now).
- Changing the benchmark's labels or pretending it's publish-credible.
- Endless iteration: HARD CAP 2 iterations per target. If not clearly better after 2, revert +
  document, stop.
- Frozen contract changes, RLS, quota. Any paid service.

## Verification
```yaml
- name: tests
  cmd: uv run pytest -v
  required: true
- name: eval-replay
  cmd: uv run python eval/runner.py --replay   # deterministic after cassette re-record
  required: true
- name: lint
  cmd: uv run ruff check .
  required: true
```

## Decision authority
ESCALATE: the eval sign-off after EACH target (GG does the human spot-check — the real gate; the
metric alone doesn't decide); the cassette re-record (needs a clean Groq window; all-or-nothing);
whether to KEEP or REVERT each change (GG's call based on spot-check + metric). Prompt edits +
fixtures + re-record = autonomous up to the escalation points.

## Hard rules
- BOUNDED: max 2 iterations per target. Not clearly better → revert, don't keep tuning.
- Tune to the RUBRIC / correct output, NEVER to the LLM labeler's opinions.
- A prompt change REQUIRES a full clean cassette re-record (all fixtures, one pass) before the
  eval is trustworthy — partial/stale cassettes forbidden.
- Urgency: no regression on existing-correct high-calls (test-pinned).
- Human spot-check of changed cases is the real gate; the metric is a hint. Both required to KEEP.
- $0, free tier; frozen contracts intact; full suite + eval-replay green.

## Success criteria
- [ ] Target A: either a KEPT hi-en improvement (metric >~3pp up AND GG spot-check confirms better,
      cassettes re-recorded, eval green) OR an honest REVERT with "no bounded gain" documented.
- [ ] Target B: harm-in-positive-tone classifies HIGH (regression fixtures added + green), no
      regression on existing correct calls, GG confirms via spot-check. (More likely to yield real
      gain since the rubric is adjudicated.)
- [ ] Cassettes re-recorded cleanly for any kept prompt change; eval-replay deterministic + green.
- [ ] No endless tuning: each target capped at 2 iterations; reverts documented honestly.

## Build order
1. Target B FIRST (better-measured, adjudicated rubric, clear pass/fail): inspect en-013-type
   cases → prompt change for harm-over-tone → regression fixtures → re-record → re-run → GG
   spot-check → keep/revert.
2. Target A SECOND (noisier): inspect the actual hi-en mislabeled cases → 1-2 targeted iterations
   → re-record → re-run → GG spot-check the CHANGED cases → keep only if metric >3pp AND spot-check
   confirms; else revert.
3. Document outcomes honestly (kept/reverted per target, with the human-judgment reasoning).
