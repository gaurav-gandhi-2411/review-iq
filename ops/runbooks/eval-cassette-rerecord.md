# Runbook: Re-recording Eval Cassettes Against Live Groq

**⚠️ UNTIL THE STEPS BELOW ARE COMPLETED, EVERY RE-RECORD RUNS ON PRODUCTION GROQ QUOTA.
THERE IS NO ISOLATED BENCHMARK KEY TODAY.** A prior session's commit message (`6002a7a`,
2026-09-05) claimed the 49-fixture re-record used "Option A: isolated benchmark key,
GROQ_API_KEY_BENCHMARK, never the production key." That claim could not be verified and is
now believed false — see "What actually happened, 2026-09-05" below. This file did not
exist before 2026-09-10; it is written from scratch this session, not corrected from a
prior version, because no prior version existed anywhere in this repo.

---

## Current state: NO isolated key exists (VERIFIED 2026-09-10)

Checked three places, all negative:

| Where | Result |
|---|---|
| `gh secret list --repo gaurav-gandhi-2411/review-iq` | No `GROQ_API_KEY_BENCHMARK` (only `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, `DB_BACKUP_ENCRYPTION_KEY`, `SUPABASE_DIRECT_URL`) |
| `gcloud secrets list --project reviewiq-prod-260813` | Only `groq-api-key` (singular, production) — no benchmark variant |
| `benchmark/vernacular_v2/.env.benchmark.local` (the gitignored local file `benchmark_groq_key.py`'s own setup instructions describe) | Does not exist on disk |

`benchmark/vernacular_v2/benchmark_groq_key.py` is real code with a real loader
(`load_benchmark_groq_key()`), but **`eval/runner.py`'s cassette-recording path never
imports or calls it** — confirmed by grep across `eval/`, `app/`, `scripts/`: zero hits for
`benchmark_groq_key`, `load_benchmark_groq_key`, or `GROQ_API_KEY_BENCHMARK` outside the
`benchmark/vernacular_v2/` scripts that module was built for. `eval/runner.py`'s recording
call always goes through `app/core/providers/groq.py`'s `GroqProvider`, constructed from
`get_settings().groq_api_key` — i.e. the standard `GROQ_API_KEY` env var, which is
production's key both locally (if exported from `.env`) and on Cloud Run (Secret Manager's
`groq-api-key`). There is no code path today that would have routed a cassette-recording
run through anything else, regardless of what any `.env.benchmark.local` file might have
contained.

## What actually happened, 2026-09-05 (BELIEVED, not fully provable either way)

Given the above, the 2026-09-05 re-record (commit `6002a7a`) almost certainly ran against
whatever `GROQ_API_KEY` was set to in the environment that invoked it — which, absent any
working isolation mechanism, means production's key, contradicting that commit's own
claim. The one thing that can't be ruled out from repo artifacts alone: a human could have
manually exported a *different* key's value under the literal `GROQ_API_KEY` env-var name
for that one shell session, achieving real isolation without leaving any trace in this
repo. There is no way to confirm or rule this out from what's committed here — only Groq's
own per-key usage dashboard (human-only access) could settle it, and that hasn't been
checked. State this as BELIEVED, not VERIFIED, wherever it's cited.

## Why "a second API key" does not actually isolate anything — read before creating one

**Groq's rate limits (RPM/TPM/RPD/TPD) are enforced at the organization level, not per
API key** (verified against `https://console.groq.com/docs/rate-limits`: "Rate limits
apply at the organization level"). A second key created under the *same* Groq
account/organization as production draws from the exact same shared quota pool — it is
a different credential, not a different budget. `benchmark_groq_key.py`'s own design
(a second key, same account, gitignored locally) does not achieve the isolation its own
docstring claims, independent of whether that mechanism is ever wired into `eval/runner.py`.
This is a real, pre-existing design flaw in that module, not just a gap in this runbook.

## What genuine isolation requires (GG console work — exact steps)

1. Sign up for a **new Groq account under a different email address** than the one backing
   review-iq's production account — not a second key on the same account (see above; that
   does not isolate anything). Go to `https://console.groq.com/login` and use a fresh email.
2. On the new account, create an API key at `https://console.groq.com/keys` — name it
   something identifiable, e.g. `review-iq-eval-benchmark`.
3. Confirm the new account is on Groq's free tier (default for a new signup; no payment
   method needed for this to work).
4. Locally (never committed — this repo's `.gitignore` already covers `.env.*.local`):
   create `benchmark/vernacular_v2/.env.benchmark.local` with one line:
   `GROQ_API_KEY_BENCHMARK=gsk_<the new account's key>`.
5. **This alone is not sufficient to isolate a cassette re-record** — `eval/runner.py`
   still has no code path that reads `GROQ_API_KEY_BENCHMARK`. Until that's built (a small,
   separate code change, not done this session per the no-prompt/no-eval-code-change
   constraint), the *operational* workaround is: export the new account's key under the
   *same* `GROQ_API_KEY` name, for the duration of the recording command only, in a shell
   that touches nothing else:
   ```bash
   GROQ_API_KEY=gsk_<the new account's key> EVAL_CASSETTE_MODE=record \
     uv run python -m eval.runner --routed
   ```
   Do not export this in a persistent shell profile or `.env` file — set it inline for this
   one command, then close the shell.
6. Verify isolation actually worked: check both accounts' usage dashboards
   (`console.groq.com` → Usage, logged in as each account) immediately after the run. The
   new account's token usage should move by roughly the recorded run's total
   (~80,115 tokens_in + ~38,367 tokens_out for the current 49-fixture set, per `6002a7a`'s
   own reported totals); production's usage should show zero movement from this run.

## Cost/quota to budget for a full re-record

49 fixtures, current models (`openai/gpt-oss-20b`/`120b`): **80,115 tokens_in + 38,367
tokens_out** measured on the 2026-09-05 run (source: commit `6002a7a`'s own message,
cross-checked against the committed `eval/results.json`/`cassettes.json` — not
independently re-measured this session, since re-measuring would itself spend quota).
Groq's free tier is request-per-minute and token-per-minute limited, not a fixed daily
allowance that this number consumes visibly against — the recorder's own incremental
load-mutate-save design (record what you can, resume later) is the correct way to handle
hitting a per-minute ceiling mid-run, not a pre-flight budget check.

## Procedure (once an isolated key exists and is wired in)

1. `EVAL_CASSETTE_MODE=record` + the isolated key (see step 5 above) +
   `uv run python -m eval.runner --routed`.
2. Verify **0 fixture errors** in the run output and a full cassette count (49 new keys
   added, check via `git diff --stat eval/cassettes/cassettes.json` before committing).
3. `EVAL_CASSETTE_MODE=replay uv run python -m eval.runner --routed` as an independent
   sanity check — should reproduce byte-identical scores with zero network calls.
4. Report the new scores with paired bootstrap CIs (`eval/bootstrap.py::bootstrap_ci`).
   Expect them to move. Do not tune the prompt to recover a prior number — see
   `docs/architecture/adr/0001-eval-gate-and-prompt-version-reconciliation.md`.
5. Commit `eval/cassettes/cassettes.json`, `eval/results.json`, `eval/results/latest.json`,
   `eval/report.md` together, in one commit, with the exact key identity and token totals
   stated in the commit message from this run's own console output — not copied from a
   prior run's numbers.
