# Staged fixtures — pending a real Groq cassette recording

These 83 English fixtures (`eval/consensus/run_consensus.py`'s growth output, ADR 0002) have
real multi-LLM-consensus ground truth, but their `eval/cassettes/cassettes.json` entries were
captured via OpenRouter (`meta-llama/llama-3.1-8b-instruct`), not production's Groq
(`llama-3.1-8b-instant`), after a Groq daily-token-budget exhaustion incident during recording
(ADR 0003). Scoring them against a different model's output would make the reported CI-gate
accuracy a measurement of OpenRouter's model, not of what review-iq actually ships — so they are
deliberately kept **out of `eval.runner`'s scanned path** (`_collect_fixture_paths` in
`eval/runner.py` only globs flat files plus `hi-en/` and `hi/` — a directory named anything else,
including this one, is invisible to it) until they have real Groq-recorded cassettes.

**To promote a fixture out of staging:** re-record its cassette against production's real
`GROQ_API_KEY` (`EVAL_CASSETTE_MODE=record uv run python -m eval.runner --routed` — mind the
daily TPD budget, see ADR 0003) and `git mv` it back into `eval/fixtures/`. Do this in a batch
small enough to fit the day's remaining token budget, not all 83 at once blind.

**If re-running `eval/consensus/run_consensus.py` to grow the eval set further:** it writes new
fixtures directly into `eval/fixtures/`, not here — a fresh run will hit the same problem for
any newly-grown fixture until you either record its cassette in the same pass or move it here
manually.
