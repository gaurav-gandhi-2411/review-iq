# ADR 0003: Cassette Provenance During Groq Quota Exhaustion

**Status:** Accepted
**Date:** 2026-07-30
**Scope:** Wave 1 Section B ("Kill hand-labeling") follow-up of
`docs/specs/wave1-commercialization.md`. Covers only how the 83 new consensus-grown
fixtures (ADR 0002) got their cassette entries after the standing Groq-recording
procedure hit a live production-key quota wall. Does not touch Section A, the tiered
router, CI gate thresholds, or `app/core/providers/cassette.py`'s schema.

## Context

`eval/README.md`'s standing rule is: record cassettes with
`EVAL_CASSETTE_MODE=record uv run python -m eval.runner --routed`, which calls the real
`GROQ_API_KEY` — the same key Cloud Run's live customer-facing service uses. Attempting
this for the 83 new fixtures exhausted Groq's `llama-3.3-70b-versatile` daily token
budget (99,770/100,000 used) partway through, after recording only 43/132 fixtures — a
real quota-consumption incident against a production key, not a routine maintenance
action, already flagged to and acknowledged by the user (see the `chore: wip checkpoint`
commit for the full incident writeup). The 210 genuinely-recorded cassette entries from
that attempt were kept (additive, non-destructive); the interim `eval/results.json` /
`eval/report.md` / `eval/results/latest.json`, which reflected a quota-exhausted partial
run (27.3% overall, most hi/hi-en fixtures scoring 0% due to the exhausted large-tier
quota, not a real accuracy signal), were left in place pending this follow-up.

The user authorized a substitute recording path for this session only: OpenRouter
(`OPENROUTER_API_KEY`) as primary, Gemini (`GEMINI_API_KEY`) as secondary fallback,
neither of which touches the Groq production key. Querying OpenRouter's `GET
/api/v1/models` live (2026-07-30) confirmed the model slugs `meta-llama/llama-3.1-8b-
instruct` and `meta-llama/llama-3.3-70b-instruct` — the same nominal Llama family/weights
as Groq's `llama-3.1-8b-instant` / `llama-3.3-70b-versatile`, served through a different
backend (OpenRouter routes to whichever upstream host it has provisioned, e.g.
DeepInfra) — **not** guaranteed bit-identical to what Groq's own hosting would return for
the same prompt.

`scripts/record_cassettes_via_fallback.py` (new, standalone, not wired into CI) found 83
missing small-tier cassette keys — exactly the 83 new fixtures, confirming the pre-
existing 49 fixtures' cassettes were already complete. It recorded all 83 via OpenRouter
on the first attempt (0 schema-validation retries, 0 failures, 0 Gemini fallbacks
needed). None of the 83 fixtures' real (substitute-provider) small-tier responses
triggered the router's escalation logic (`app/core/routing_policy.escalation_triggers`),
so no large-tier cassette was needed for any of them — a materially different, and more
accurate, outcome than the original quota-exhausted Groq attempt, where the missing
small-tier cassette was itself misread by the router as "small model exhausted,"
*forcing* an escalation attempt to the (also-missing) large tier on every one of those 83
fixtures. That earlier escalation pattern was a replay-mode artifact of the missing
cassette, not evidence that the real pipeline would have escalated those fixtures.

A full `EVAL_CASSETTE_MODE=replay --routed` run after recording confirmed **zero**
missing-cassette errors across all 132 fixtures: overall 74.6%, en 73.3% (FAIL, gate
80%), hi 80.7% (PASS), hi-en 80.9% (PASS). These numbers are **substitute-provider**
numbers — mixing 49 real-Groq-recorded fixtures with 83 OpenRouter-recorded fixtures —
not a clean Groq-only measurement, and are meaningfully lower than the previously
reported 83.8%/49-fixture figure (a different, smaller, all-Groq fixture set).

## Decision

1. **Cassette schema is untouched.** `eval/cassettes/cassettes.json` still stores only
   `{raw, tokens_in, tokens_out}` per key — no provenance field added there, preserving
   the exact-match key contract every existing consumer depends on.
2. **Provenance lives in a separate, additive sibling file**:
   `eval/cassettes/cassette_provenance.json`, mapping each of the 83 substitute-recorded
   keys to `{source, model_requested, recorded_at, reason}`. `source` is `"openrouter"`
   for all 83 entries this run (no `"gemini"` entries were needed).
3. **`eval/README.md` gets an explicit, contributor-facing disclosure section** stating
   which keys were recorded via substitute provider, why, and that a real Groq
   re-recording pass is still owed once the daily TPD window resets. This is an
   engineering/contributor doc, not a customer-facing surface — no changes to
   `README.md`'s main body or `site/index.html`.
4. **The substitute-provider run's numbers (74.6% overall / 73.3% en / 80.7% hi / 80.9%
   hi-en) are reported here, honestly, but NOT written into `eval/results/latest.json`,
   `eval/results.json`, or `eval/report.md`, and NOT rendered into README's metrics
   blocks.** Those three files were regenerated as a side effect of running the
   replay-verification command, then explicitly reverted back to their prior committed
   (Groq-only, partial-exhausted-run) content in this same session — restoring them to
   HEAD, not carrying the substitute-run numbers forward. Whether the substitute-run
   number should become the interim officially-reported figure, or whether the
   previously-reported 83.8%/49-fixture number should remain authoritative until a clean
   all-Groq measurement exists, is an explicit judgment call **left to the orchestrator**,
   not resolved unilaterally here (see this session's report for the flagged decision).

## Consequences

**Positive:**
- CI's cassette-replay eval gate now has full coverage (132/132 fixtures, 0 missing-key
  errors) — the gate can run and score every fixture without any live API call.
- The provenance file makes it mechanically checkable, per key, which cassette entries
  came from Groq vs. a substitute provider — nobody has to trust a commit message alone.
- The substitute-provider recording pass incidentally *fixed* a measurement artifact:
  the original quota-exhausted Groq attempt was forcing every one of the 83 new fixtures
  through an unwanted escalation attempt (because a missing cassette reads as "model
  exhausted" to the router), which this pass's real substitute responses did not
  reproduce — 0 of 83 fixtures genuinely need a large-tier cassette.

**Negative / residual risk:**
- 83 of 132 fixtures' cassette entries (63%) are not Groq responses. Any score computed
  from the full 132-fixture set today reflects OpenRouter's serving of the same nominal
  Llama weights, not Groq's — a real, disclosed measurement gap, not a silent one.
- The substitute-provider full-coverage run scored materially lower (74.6% overall, en
  73.3% FAILING the 80% per-language gate) than the previously reported clean-Groq
  83.8%/49-fixture number. This is not evidence the pipeline regressed — it is a
  different, larger, partially-different-provider fixture set — but it must not be
  quietly smoothed over either. A real Groq re-recording pass is the only way to get a
  clean, comparable number for the full 132-fixture set.
- **Resolved (orchestrator, same session):** rather than leave "which number is official"
  open, the 83 substitute-provider fixtures were moved to
  `eval/fixtures/_pending_groq_cassette/` — outside `eval.runner`'s scanned path
  (`_collect_fixture_paths` only globs flat files plus `hi-en/`/`hi/`), so they
  contribute nothing to `eval/results.json` / CI's gate. The clean, all-Groq
  **49-fixture / 83.8%** figure is restored as the sole current gating number; the
  83.8-vs-74.6% ambiguity does not exist in the committed state. When a real Groq
  re-recording pass is done, `git mv` the promoted fixtures back into `eval/fixtures/`
  and re-run `eval.runner` normally — timing of that re-recording is still an open,
  unscheduled follow-up (gated on the daily TPD window), just no longer an ambiguity
  about which number gates CI today.

## Alternatives considered

1. **Wait for the Groq daily quota to reset and record cleanly.** Rejected as the sole
   path for this session — the reset window is unknown/unbounded, per the standing
   "no idle-waiting" policy, and the user explicitly authorized a substitute-provider
   path rather than parking the work.
2. **Use Gemini as the sole substitute** (skip OpenRouter). Rejected — Gemini's free
   tier is documented elsewhere in this repo as `limit: 0` (unprovisioned) on at least
   one path, and using it as primary rather than fallback would forgo the closer
   nominal-model match OpenRouter's Llama-family slugs offer. Gemini is kept as the
   secondary fallback exactly as specified, and ended up unused (0/83) since OpenRouter
   succeeded on every fixture.
3. **Silently regenerate `eval/results/latest.json` with the substitute-provider run's
   numbers**, treating 74.6% as the new official figure. Rejected — this would present a
   mixed-provider measurement as if it were the same clean Groq gate previously reported,
   without disclosure, which is exactly the kind of silent-drift failure ADR 0001 exists
   to prevent. Flagging the judgment call explicitly (Decision #4) instead.
4. **Add a `source`/`provenance` field directly to `cassettes.json`'s schema.** Rejected
   — every existing consumer of that file (`GroqProvider.complete`'s replay path, the
   eval runner, potentially other tooling) depends on its exact `{raw, tokens_in,
   tokens_out}` shape; changing it is unnecessary scope creep for a one-off recording
   gap and risks a schema-drift bug for no real benefit over a sibling file.
