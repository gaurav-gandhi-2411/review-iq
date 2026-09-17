# Eval Runbook

## What the CI eval gate is

The CI eval (`.github/workflows/eval.yml`) runs in **cassette-replay mode**
(`EVAL_CASSETTE_MODE=replay`): it replays recorded LLM responses from
`eval/cassettes/cassettes.json` instead of calling Groq. The gate is therefore:

- **Deterministic** — same inputs → same scores on every run.
- **$0 / free-tier-safe** — zero live API calls; immune to the Groq free-tier daily-quota
  exhaustion that otherwise takes a *live* eval red regardless of code quality.
- **Still a real gate** — it validates the extraction logic + prompts against the recorded
  model behavior and the fixture ground truth. Pass = **<!-- METRICS:START:gate_summary -->overall ≥ 76%, en ≥ 77%, hi-en ≥ 75%<!-- METRICS:END -->**
  (`PASS_THRESHOLD` / `PER_LANG_THRESHOLD` in `eval/runner.py`).

No `GROQ_API_KEY` is set in CI, so a **missing cassette fails loudly** (no silent live call).

## Gate thresholds are CURRENT MEASURED PERFORMANCE, not quality targets

<!-- METRICS:HISTORICAL -->
**Reset 2026-09-10 (Session 5 P4), re-derived same day (Session 6 P4a).** The gate values
above are the honest, measured result of the openai/gpt-oss-20b/120b re-record (PR #133 /
commit `6002a7a`), after a real scoring-harness bug fix (see `eval/runner.py`'s
`_check_security` docstring): fixture 003 was being hard-zeroed on a mislabeled
"SECURITY FAIL" even when the prompt-injection attempt had zero effect, masking its real
(mostly correct) field-level score. Fixing that moved the measured baseline itself up
(overall 77.6%→79.3%, en 75.0%→78.1%) — the numbers below are against the corrected
baseline. The *previous* gate (overall ≥83%, all languages ≥80%) was itself measured under
the now-deprecated `llama-3.1-8b-instant`/`llama-3.3-70b-versatile` and never re-validated
against the models actually deployed — it looked like a real target but was actually
stale, and stayed green for weeks purely because nothing forced a re-measurement (see the
model-drift incident this exists to prevent, `scripts/check_eval_model_matches_config.py`).

**Why the old 86.2%/83.8% figures don't mean what they look like** (both reasons hold,
independently — this is not "not contamination"; it is contamination PLUS a second,
separate inflation source):
1. **Fixture contamination**: fixtures were added in the same commits as the prompt fixes
   they test (see ADR 0001's D3 discussion) — the eval measured "does the prompt still
   pass what it was tuned against," not held-out generalization.
2. **Llama-specific calibration fitting**: separately, the v2.3 prompt's few-shot hedge
   examples (when to say `sentiment: "mixed"` or `buy_again: null` vs. commit to an
   answer) were validated against Llama's specific response tendencies. A prompt fitted to
   one model's calibration measuring 86.2% under that model does not mean 86.2% describes
   a transferable capability — it can (and, per the gpt-oss re-record, did) measure a
   model-specific artifact on top of the fixture contamination, not instead of it.

**Aspirational targets** (tracked here so they are not lost, separate from the live gate):

| Language | Current gate (measured) | Aspirational target | Gap |
|---|---|---|---|
| Overall | 79% | 83% | 4pp |
| en | 77% | 80% | 3pp |
| hi | 80% | 80% | 0pp (already at target) |
| hi-en | 80% | 80% | 0pp (already at target) |

The English gap is diagnosed, not mysterious, and it is **calibration, not capability**
(Session 5 P3 + Session 6 P1, per-field comparison old vs. new models, real predicted/
expected values): gpt-oss abstains (`buy_again: null`, `sentiment: "mixed"`) far more often
than Llama did on English (`buy_again` coverage 81%→50%, `sentiment` coverage 85%→46%) —
but accuracy on the answers it DOES commit to is flat or slightly improved (`buy_again`
100%→100%, `sentiment` 91.3%→91.7%). Flat accuracy charges an abstention the same as a
wrong answer; for an extraction service, an abstention is routable to a human review queue
and a confident wrong answer is not — these are not equivalent failure modes, and this
metric does not distinguish them.

**Correction (Session 7 P2, `docs/specs/wave1-coverage-abstention-analysis.md`):** the raw
coverage numbers above overstate the real hedge rate, because they count `sentiment:
"mixed"`/`buy_again: null` as an abstention even when that hedge value IS the ground truth
(a correct answer, not a hedge) — 43.8% of raw `sentiment` hedges and 28.6% of raw
`buy_again` hedges fall in this bucket on the 49-fixture set. Of the *real* hedges only
(hedge value != ground truth), a blind 2-judge consensus panel (#140) adjudicated
decidability: `buy_again` has real, recoverable headroom (5/10 real hedges resolve to the
panel's unanimous, ground-truth-matching answer — +11.6pp on that field's own score if
fixed); `sentiment` has ~zero (0/9 — the panel either also hedges or splits, and in over
half of the remainder it *agrees with the model's hedge over the original ground truth's
directional label*, which points at a fixture-labeling question, not a model-hedging one).
Read the full per-fixture breakdown before attempting to close this gap with prompt
changes: re-tuning the prompt to make gpt-oss hedge less on English would be re-fitting it
to gpt-oss's specific calibration the same way v2.3 was fitted to Llama's — the exact
mistake reason #2 above describes, just aimed at a different model — and for `sentiment`
specifically, the evidence above says there is no real gap to fit toward.

**This gate is a CHANGE DETECTOR, not a quality bar — read before touching a threshold.**
Under cassette replay the same 49 fixtures produce the same scores every run; there is no
run-to-run noise for a real regression to hide in, so the tight margins below (0.3-1.3pp)
are deliberate, not an oversight. A drop of even a fraction of a point means something in
scoring, prompts, or fixtures actually changed. **Do not loosen these thresholds just
because ordinary corpus growth trips them later** — more fixtures means naturally noisier
per-fixture scores, and that noise WILL eventually brush against a 0.3-1.3pp margin. That
is the margin doing its job, not the margin being wrong. If corpus growth is genuinely the
reason a threshold needs to move, that is a deliberate re-baseline decision (state the new
measured number and why, the same way this section does), never a threshold nudge to make
a red CI run green again.

**When a regression would trip a gate now:** overall (79.3% measured) needs to drop >0.3pp
to fail its 79% gate; en (78.1%) needs to drop >1.1pp to fail its 77% gate; hi (81.3%) needs
to drop >1.3pp to fail its 80% floor; hi-en (80.6%) needs to drop >0.6pp to fail its 80%
floor. Overall has the tightest margin (0.3pp) of the four — hi has the
most headroom (1.3pp).
<!-- /METRICS:HISTORICAL -->

## ⚠️ Standing rule — cassettes must not drift from real model behavior

Cassette-replay freezes a snapshot of model behavior. The CI gate is only honest if that
snapshot is current. Therefore:

- **Run a LIVE eval manually before any release**, and **whenever the prompts change**
  (`app/core/prompt.py`, `app/core/prompts/**`) or the model/router behavior changes.
- **Re-record the cassettes in the same pass**, so CI tests against *current* behavior.

A cassette set that is never refreshed after a prompt change is a silent-failure trap: CI
stays green against stale responses while real model behavior diverges from the gate.

## Commands

**Live eval** (needs Groq quota — use a full daily window, a single clean run, no pre-probe):
```bash
uv run python -m eval.runner --routed   # routed/tiered live eval (what we record from)
uv run python -m eval.runner            # direct live eval
```

**Re-record cassettes** — RECORD ALL FIXTURES IN ONE CLEAN PASS. A partial set is a
silent-failure trap (replay would pass while silently missing fixtures). Verify **0 fixture
errors** and a full cassette count before committing:
```bash
EVAL_CASSETTE_MODE=record uv run python -m eval.runner --routed
# verify: eval/results.json has no fixture errors; eval/cassettes/cassettes.json covers every fixture
git add eval/cassettes/cassettes.json eval/results.json eval/report.md
```

**Replay** (exactly what CI runs — offline, no API key, zero live calls):
```bash
GROQ_API_KEY= EVAL_CASSETTE_MODE=replay uv run python -m eval.runner
```

## Cassette format

`eval/cassettes/cassettes.json` maps `sha256(model + system_prompt + user_prompt)` →
`{raw, tokens_in, tokens_out}`. Keying on the full prompt means any prompt change produces
new keys, so a stale cassette surfaces as a missing-cassette **failure** in CI (loud), not a
silent stale pass — re-record to clear it.

## ⚠️ Known gap — some cassette entries are substitute-provider, not Groq (2026-07-30)

83 of the 132 fixture cassette entries were **not** recorded against Groq. The standing
`record` procedure above calls production's `GROQ_API_KEY` — attempting it for these 83
new (Section B consensus-grown) fixtures exhausted Groq's `llama-3.3-70b-versatile` daily
token budget mid-run (99,770/100,000), on the same key Cloud Run's live service uses. With
the user's explicit authorization, the remaining gap was filled via
`scripts/record_cassettes_via_fallback.py` — a standalone, CI-independent script that
calls OpenRouter (`meta-llama/llama-3.1-8b-instruct` / `meta-llama/llama-3.3-70b-instruct`,
the same nominal Llama weights, different serving backend) as primary, with Gemini as a
secondary fallback if OpenRouter fails. All 83 entries this run came from OpenRouter (0
Gemini fallbacks needed, 0 schema-validation retries, 0 hard failures).

- **Which keys, from where:** `eval/cassettes/cassette_provenance.json` maps every
  substitute-recorded key to `{source, model_requested, recorded_at, reason}`. Anything
  not in that file is a genuine Groq recording.
- **These are NOT guaranteed bit-identical to Groq's own responses** for the same prompt
  — same nominal model family, different hosting/serving stack. Treat any score computed
  from the full 132-fixture set as a substitute-provider measurement, not a clean Groq
  gate, until the entries below are re-recorded.
- **This is a temporary state, not a permanent design choice** — a real Groq
  re-recording pass (`EVAL_CASSETTE_MODE=record uv run python -m eval.runner --routed`,
  full clean pass per the standing rule above) should replace these 83 entries once the
  daily TPD budget window resets. See
  `docs/architecture/adr/0003-cassette-provenance-during-groq-quota-exhaustion.md` for
  the full incident writeup.
- **Resolved:** these 83 fixtures are staged in `eval/fixtures/_pending_groq_cassette/`,
  outside `eval.runner`'s scanned path (see that directory's README), so they do not
  contribute to the CI-gating score at all right now. The gate remains the clean,
  all-Groq figure reported in the main "Eval results" section above (rendered from
  `eval/results/latest.json`, not restated by hand here) until a real Groq re-recording
  promotes them. Scoring CI against a different model's output would just trade one
  metric-integrity problem for another — see ADR 0003's Consequences section for the
  reasoning.
