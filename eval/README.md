# Eval Runbook

## What the CI eval gate is

The CI eval (`.github/workflows/eval.yml`) runs in **cassette-replay mode**
(`EVAL_CASSETTE_MODE=replay`): it replays recorded LLM responses from
`eval/cassettes/cassettes.json` instead of calling Groq. The gate is therefore:

- **Deterministic** — same inputs → same scores on every run.
- **$0 / free-tier-safe** — zero live API calls; immune to the Groq free-tier daily-quota
  exhaustion that otherwise takes a *live* eval red regardless of code quality.
- **Still a real gate** — it validates the extraction logic + prompts against the recorded
  model behavior and the fixture ground truth. Pass = **<!-- METRICS:START:gate_summary -->every per-language bucket ≥ 80% AND
  overall ≥ 83%<!-- METRICS:END -->** (`PASS_THRESHOLD` / `PER_LANG_THRESHOLD` in `eval/runner.py`).

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
