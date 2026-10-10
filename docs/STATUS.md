# Samidha Reviews: status

Updated at the end of every session. Last update: 2026-10-10 (S21). Labels: VERIFIED = observed this
session or by a committed artifact; BELIEVED = inferred or stated by someone else, not observed.

## Live in production (Cloud Run `review-iq`, asia-south1, revision `review-iq-00062-b47`, 100 percent of traffic)

| Item | State | Evidence |
|---|---|---|
| Primary LLM | Groq, **dedicated org** (`groq-api-key-samidha`) | VERIFIED: service description; live `/demo/extract` 200 |
| Failover | Shared-org Groq, `openai/gpt-oss-120b` (`SECONDARY_PROVIDER_KIND=groq`, ADR 0038) | VERIFIED offline with an invalid primary key: served by `provider=secondary`, 4.75 s |
| OpenRouter | Unbound from the service; key still in old revisions | VERIFIED: bindings; old revisions BELIEVED to carry it |
| Supabase service-role key (leaked-key incident) | CLOSED 2026-10-10 | VERIFIED: removed from Cloud Run (step d); GG disabled legacy API keys (step f); probe `python -I g4_jwt_dead.py` returned HTTP 401 `Legacy API keys are disabled` on `/rest/v1/` for the legacy service_role JWT (role service_role, ref enqpluazgxewepchdeut, exp 2094); before step f the same probe returned 200. Zero consumers (no Cloud Run service or job bound it; code only falls back to it when the anon key is empty, and `SUPABASE_ANON_KEY` is set); secret `supabase-service-role-key` (1 version) deleted, list now shows 0. Anon path: auth endpoint with the publishable key returns 400 `invalid_credentials` for bad credentials (key accepted), `/api/health` 200. BELIEVED: a full login with a real account was not run (no credentials). The Secret Manager value is assumed to be the leaked JWT (same ref and role; not provable) |
| Migrations | 46/46 applied; `20261009000003` (weekly_digest CHECK + `list_orgs_with_weekly_digest`) applied 2026-10-10 as review_iq_migrator; `push.py --verify` PASS=99 | VERIFIED 2026-10-10; Migration Drift Check and Schema Drift Check workflows green after apply |
| Alerts | Suppression list (#315) and urgent-email coalescing (#316) live; unconfirmed in practice (no org has an alert preference enabled) | VERIFIED counts below |
| Schedulers | ingest-tick ENABLED; digest-daily and detector-sweep PAUSED; no weekly job exists yet | VERIFIED |
| Classification engine | `engine/` merged to main (core, runners, serving code, Kaggle tooling); not deployed anywhere | VERIFIED: PRs 327, 331-337 merged; E2 report `docs/reports/e2-benchmark.md` (in review) |

## Measured

| Claim | Number | Source |
|---|---|---|
| Held-out accuracy, full pipeline vs plain-prompted Llama 3.3 70B | <!-- METRICS:HISTORICAL -->79.6% vs 78.3%<!-- /METRICS:HISTORICAL --> (extraction accuracy is not a moat) | BELIEVED: figures as stated in the S21 brief; no committed artifact in `eval/results/` was found that contains the plain-Llama number, so it is unreproduced here |
| Groq free limits on both orgs | 1,000 req/day and 8,000 tokens/min per model (20b, 120b, qwen3.8-27b) | VERIFIED response headers 2026-10-10 |
| Groq daily token pool | 200K/model/day | BELIEVED (Groq docs; not exposed in headers) |
| Failover latency (Groq to Groq, 120b) | 4.75 s for one 259-in/437-out call | one VERIFIED sample |
| Load test (mock LLM, one local uvicorn worker) | ~326 req/s at 0 ms provider latency, no error knee to c=128 | PR #323 (draft), `reports/load/s20-run.json`; DB path and Cloud Run unmeasured |
| Free OpenRouter models for evals | 1 of 4 probed answered; ~50 req/day cap BELIEVED | S20 probe; N1b frontier arm not possible free |
| Classification engine E2 (public benchmarks) | See `docs/reports/e2-benchmark.md`; every table is rendered from `reports/engine/e2/*.json` | VERIFIED artifacts, commits inside each file |
| U4d injection twin control | 32 of 74 pairs after day 2 (day 1 on 2026-10-09, day 2 on 2026-10-10 UTC, 94,765 tokens on gpt-oss-20b); partial, not a finding | `eval/results/injection_twin_control*.json` on `exp/s20-u4d-twin-control` (PR 318) |
| Usage | 19 orgs, 1 with a notification email, 0 with an alert preference enabled, 0 orgs with an extraction in the last 7 days | VERIFIED read-only counts 2026-10-10 |

## Open, and what blocks each

| Item | Blocked on |
|---|---|
| Weekly digest (#317 merged, migration applied) | Create the `review-iq-digest-weekly` Scheduler job (paused) per `ops/runbooks/weekly-digest.md`; a customer must enable weekly_digest; the Resend webhook secret and registration and `DASHBOARD_URL` are the same prerequisites as the daily digest |
| Un-pausing digest-daily | Nothing is enabled to send (0 digest orgs); needs a customer to enable it plus the Resend prerequisites |
| `cache_env.sh` (#330) | GG hand-merge: gate 4 matches the filename on `env`; the file only exports cache directories |
| Size-blocked PRs (gate 3 only, CI green on last update): #278, #314, #323, #308, #268, #289, #271, #273 (+ #318 gate 1 too) | The live gate has no body-based waiver; a per-PR entry in `PR_WAIVER_ALLOWLIST` with GG approval, or a split. Each needs `gh pr update-branch` first (main moved) |
| Gate-4 PRs: #292, #291 (shopify_auth), #288 (deploy workflow), #269 (auth + migration, conflicting), #267 (ops runbook paths), #262 (runbook paths + 9K lines), #275 (auth, conflicting) | GG review |
| #298 (reply prompt v2.2) | Gate-clean but its own body says the prompt eval must run before merge (cassette re-record, Groq quota) |
| #293 | Stacked on #291/#292 (base is not main) |
| #232 (anthropic 0.102 to 1.11 major), #233 (structlog), #106 (tailwind 4, web-build fails) | Dependabot: major bumps need a human or the migration plan |
| OpenRouter key rotation | GG (old revisions carry it) |
| English labelling fixtures | Free OpenRouter yield too low; see the certification ADR |
| Tailwind 4 migration (#106) | Plan in `docs/ops/tailwind4-migration-plan.md`, before the braces allowlist expires 2026-12-31 |
| U4d | Days 3 to 5 (about 42 pairs left, about 15 per UTC day, canary first) |
| claude-config hosted CI | GitHub Free private-repo minutes (resets about 2026-11-01); use `scripts/local_ci_attest.py` |
| Free-tier capacity of the dedicated org | Needs a burn to the first 429; not done to protect production |
| Engine deployment (Cloud Run CPU) | GG decision: it is a new service; latency and cost are measured in-process only |

## GG must do

See the end of the latest session report; this table is regenerated each session.
