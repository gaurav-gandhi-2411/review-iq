# Eval Runbook

## What the CI eval gate is

The CI eval (`.github/workflows/eval.yml`) runs in **cassette-replay mode**
(`EVAL_CASSETTE_MODE=replay`): it replays recorded LLM responses from
`eval/cassettes/cassettes.json` instead of calling Groq. The gate is therefore:

- **Deterministic** — same inputs → same scores on every run.
- **$0 / free-tier-safe** — zero live API calls; immune to the Groq free-tier daily-quota
  exhaustion that otherwise takes a *live* eval red regardless of code quality.
- **Still a real gate** — it validates the extraction logic + prompts against the recorded
  model behavior and the fixture ground truth. Pass = **<!-- METRICS:START:gate_summary -->overall ≥ 77%, en ≥ 74%, hi-en ≥ 80%, hi ≥ 80%<!-- METRICS:END -->**
  (`PASS_THRESHOLD` / `PER_LANG_THRESHOLD` in `eval/runner.py`).

No `GROQ_API_KEY` is set in CI, so a **missing cassette fails loudly** (no silent live call).

## Gate thresholds are CURRENT MEASURED PERFORMANCE, not quality targets

<!-- METRICS:HISTORICAL -->
**Reset 2026-09-10 (Session 5 P4).** The gate values above are the honest, measured result
of the openai/gpt-oss-20b/120b re-record (PR #133 / commit `6002a7a`), not a bar chosen for
where the product should be. The *previous* gate (overall ≥83%, all languages ≥80%) was
itself measured under the now-deprecated `llama-3.1-8b-instant`/`llama-3.3-70b-versatile`
and never re-validated against the models actually deployed — it looked like a real target
but was actually stale, and stayed green for weeks purely because nothing forced a
re-measurement (see the model-drift incident this exists to prevent, `scripts/check_eval_model_matches_config.py`).

**Aspirational targets** (tracked here so they are not lost, separate from the live gate):

| Language | Current gate (measured) | Aspirational target | Gap |
|---|---|---|---|
| Overall | 77% | 83% | 6pp |
| en | 74% | 80% | 6pp |
| hi | 80% | 80% | 0pp (already at target) |
| hi-en | 80% | 80% | 0pp (already at target) |

The English gap is diagnosed, not mysterious: a concentrated regression in exactly two
fields (`sentiment`, `buy_again`) where gpt-oss hedges toward `mixed`/`null` on cases the
v2.3 prompt's few-shot examples — tuned against Llama's specific response tendencies —
expected a committed answer. See the Session 5 diagnosis (per-field comparison in the PR
history) before attempting to close this gap with prompt changes; re-tuning against the
new model without understanding the mechanism risks rebuilding the same kind of
model-specific contamination this re-record just exposed.

**When a regression would trip a gate now:** overall (77.6% measured) needs to drop >0.6pp
to fail its 77% gate; en (75.0%) needs to drop >1.0pp to fail its 74% gate; hi (81.3%) needs
to drop >1.3pp to fail its 80% floor; hi-en (80.6%) needs to drop >0.6pp to fail its 80%
floor. Overall and hi-en have the tightest margins (0.6pp each) of the four — hi has the
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
