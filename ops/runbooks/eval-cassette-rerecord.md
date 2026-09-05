# Eval Cassette Re-Record Runbook (Quota-Safe)

**Project:** `reviewiq-prod-260813` · **Trigger:** Groq deprecated `llama-3.1-8b-instant` /
`llama-3.3-70b-versatile` (2026-08-16); production now runs `openai/gpt-oss-20b` /
`openai/gpt-oss-120b` (no env override on the live Cloud Run service — verified via
`gcloud run services describe`) but `eval/cassettes/cassettes.json` was never re-recorded
to match. Every replay against the current models fails 100% of fixtures with `No
cassette for key ...`.

**Status: PLANNED, NOT EXECUTED.** This runbook exists so the re-record can happen the
moment GG gives the go-ahead, without designing the safety sequence under time pressure.
Nothing below has been run. Do not run any step marked LIVE without explicit sign-off.

---

## Why this needs a runbook at all, not just "re-run the recorder"

`POST /demo/extract` (keyless, public) and every real customer's `/v2/extract` call share
**the exact same Groq API key** and its **one** free-tier daily budget: 200,000
tokens/day, 1,000 requests/day for both `openai/gpt-oss-20b` and `openai/gpt-oss-120b`
(verified live against `console.groq.com/docs/model/...`, 2026-09-05). A recording run
that uses this same key competes with real production traffic for that budget on the day
it runs. Sizing the risk (measured, not guessed) is the point of this document.

## Measured cost of a full re-record (P3a)

Real per-language average token cost, measured from this repo's own `--routed`
cassette-replay eval run (Session 2), grouped by language (n=27/7/15, 49 total):

| Language | n | avg tokens_in | avg tokens_out | avg total |
|---|---|---|---|---|
| en | 27 | 1691.8 | 141.6 | 1833.4 |
| hi | 7 | 904.9 | 114.3 | 1019.1 |
| hi-en | 15 | 1807.3 | 126.7 | 1934.1 |

**Total for all 49 fixtures: ≈ 85,648 tokens** (27×1833.4 + 7×1019.1 + 15×1934.1).

**≈ 42.8% of the 200,000-token daily budget for a single recording pass.**

Tags: **VERIFIED** that this is the real historical token cost of running these 49
fixtures through the tiered router under the *old* models. **BELIEVED, not verified**,
that the new models (`openai/gpt-oss-20b/120b`) will cost the same — prompts are
unchanged, but per-model tokenization and verbosity can differ, and
`app/core/routing_policy.py::choose_tier` now starts *every* fixture on the small model
and escalates only on a trigger (schema-validation failure / low confidence / signal
mismatch) — the new models' escalation rate is unknown until tried and could shift the
real total meaningfully in either direction. Treat 85,648 tokens (~43% of one day's
budget) as the working estimate, not a guarantee, and re-check actual usage against the
Groq dashboard mid-run if the recorder supports resuming (it does — see `_load_store()`
in `app/core/providers/cassette.py`, load-mutate-save per fixture, safe to interrupt).

**1,000 requests/day is not the binding constraint here** — a full run's real request
count is at most 49 (or up to ~98 if every fixture escalates once), nowhere near 1,000;
the token budget is what a re-record can meaningfully threaten.

## Two candidate approaches — pick one before running anything

### Option A (preferred): record on the isolated benchmark key, not production's key

`benchmark/vernacular_v2/benchmark_groq_key.py::load_benchmark_groq_key()` already
provides a fully isolated Groq key (`GROQ_API_KEY_BENCHMARK`, gitignored,
`benchmark/vernacular_v2/.env.benchmark.local`) built for exactly this class of
operation, per the standing project rule established after the 2026-07-07 incident
(unpaced benchmark load against prod's key degraded live `/v2/extract` for real
customers). A model's response to a given prompt does not depend on which valid API key
made the call — cassette content recorded on the benchmark key is identical in
substance to what would be recorded on the production key, since both hit the same
Groq-hosted model. **This approach needs no change to production traffic, no demo-cap
manipulation, and no dependency on PR #132 (the demo quota-cap PR, currently unmerged
and explicitly held for GG's review) landing first.**

Known constraint, not yet resolved: the benchmark key's own daily budget was reported as
already partially exhausted by unrelated labeling work earlier today (2026-09-05, same
UTC day this runbook is being written — Groq's daily quota had not yet reset as of this
writing, IST 17:46 / UTC ~12:16). **Before choosing Option A, check the benchmark key's
remaining headroom** — Groq does not expose a balance-check endpoint; the only way to
know is to attempt a small call and read the `x-ratelimit-remaining-tokens` response
header, which itself spends a small amount of quota. If the benchmark key is too
depleted today, the safe move is to wait for its next daily reset, not to fall back to
the production key without also completing Option B's prerequisites below.

### Option B (fallback, requires a merged PR#132 first): record on the production key with the demo cap forced to 0

If Option A is not viable (benchmark key insufficient headroom, or GG prefers the
recording to reflect the exact production credential), production's key can be used —
but only with the demo endpoint's global daily cap forced to 0 for the recording
window, so `/demo/extract` cannot add any competing token usage during the run.

**Hard prerequisite: PR #132 (`fix/wave0-demo-quota-cap`) must be reviewed, approved, and
deployed to production first.** That PR is the only thing that adds the
`DEMO_DAILY_REQUEST_BUDGET` mechanism and the `demo_daily_usage` table this option
depends on — it does not exist in production today. Per Session 3's own instruction,
PR #132 is explicitly held for GG and not to be merged autonomously; using Option B
without that merge is not possible, not just inadvisable.

Sequence, once PR #132 is live:
1. **Before**: confirm today's `demo_daily_usage` row — `SELECT * FROM
   public.demo_daily_usage WHERE usage_date = CURRENT_DATE;` — record the current
   `request_count`/token totals for a before/after diff.
2. **Set the cap to 0**: `gcloud run services update review-iq
   --project=reviewiq-prod-260813 --region=asia-south1
   --update-env-vars=DEMO_DAILY_REQUEST_BUDGET=0`, then verify the live revision actually
   serves this value (`gcloud run revisions describe <serving-revision>
   --format="value(spec.containers[0].env)"` — do not trust the deploy command's own
   success message; this exact "stale pinned revision" gotcha bit a prior session, see
   `ops/runbooks/cloud-run-deploy.md`). Confirm `POST /demo/extract` now returns 429
   immediately for a real request.
3. **Run the record**: `EVAL_CASSETTE_MODE=record uv run python -m eval.runner
   --routed` from a local checkout, using `GROQ_API_KEY` sourced from the *same*
   production secret (`gcloud secrets versions access latest --secret=groq-api-key
   --project=reviewiq-prod-260813`) — never paste it into a file, pipe directly into the
   environment for the one process invocation.
4. **Verify**: run `GROQ_API_KEY= EVAL_CASSETTE_MODE=replay uv run python -m eval.runner`
   immediately after — a clean, deterministic replay against the freshly-recorded
   cassettes should now succeed (49/49, no "No cassette for key" warnings) before doing
   anything else.
5. **Restore the cap**: revert `DEMO_DAILY_REQUEST_BUDGET` to its normal value (50, or
   whatever GG has set it to by then) via the same `update-env-vars` pattern, and verify
   the live revision again the same way as step 2.
6. **After**: re-check `demo_daily_usage`'s row for the day and Groq's own dashboard
   usage graph to confirm the recording's actual token spend against the ~85,648-token
   estimate above, and note any large deviation for the next time this runbook is used.

## What this runbook does NOT cover

- Committing the newly-recorded `eval/cassettes/cassettes.json` and regenerating
  `eval/results.json` under the new models — a normal PR after a successful recording,
  no special safety concerns beyond the recording itself.
- Re-publishing the new accuracy numbers to README/site — per Session 3's instruction,
  that is a deliberate, separate step (the corrective interim copy already shipped
  ahead of this, in PR #134, and should be updated again once real numbers exist).

## Go/no-go

**STOP HERE.** No live Groq call has been made as part of writing this runbook. Proceed
past this point only with GG's explicit go-ahead, and only after picking Option A or B
above.
