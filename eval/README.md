# Eval Runbook

## What the CI eval gate is

The CI eval (`.github/workflows/eval.yml`) runs in **cassette-replay mode**
(`EVAL_CASSETTE_MODE=replay`): it replays recorded LLM responses from
`eval/cassettes/cassettes.json` instead of calling Groq. The gate is therefore:

- **Deterministic** — same inputs → same scores on every run.
- **$0 / free-tier-safe** — zero live API calls; immune to the Groq free-tier daily-quota
  exhaustion that otherwise takes a *live* eval red regardless of code quality.
- **Still a real gate** — it validates the extraction logic + prompts against the recorded
  model behavior and the fixture ground truth. Pass = **every per-language bucket ≥ 80% AND
  overall ≥ 83%** (`PASS_THRESHOLD` / `PER_LANG_THRESHOLD` in `eval/runner.py`).

No `GROQ_API_KEY` is set in CI, so a **missing cassette fails loudly** (no silent live call).

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
