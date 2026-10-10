# Samidha Reviews: status

Updated at the end of every session. Last update: 2026-10-10 (S21). Labels: VERIFIED = observed this
session or by a committed artifact; BELIEVED = inferred or stated by someone else, not observed.

## Live in production (Cloud Run `review-iq`, asia-south1, revision `review-iq-00152-bad`)

| Item | State | Evidence |
|---|---|---|
| Primary LLM | Groq, **dedicated org** (`groq-api-key-samidha`) | VERIFIED: service description; live `/demo/extract` 200 |
| Failover | Shared-org Groq, `openai/gpt-oss-120b` (`SECONDARY_PROVIDER_KIND=groq`, ADR 0038) | VERIFIED offline with an invalid primary key: served by `provider=secondary`, 4.75 s |
| OpenRouter | Unbound from the service; key still in old revisions | VERIFIED: bindings; old revisions BELIEVED to carry it |
| Supabase service-role key | Removed from Cloud Run; live bundle ships only `sb_publishable_` | VERIFIED: bundle scan (no JWT), service description. This proves rotation step d (not served to browsers), NOT that the leaked JWT is dead: on 2026-10-10 the legacy `service_role` JWT in Secret Manager still returned HTTP 200 on `/rest/v1/` (exp 2094). Dead-key proof = same probe returns 401 after step f (`scratchpad/g4_jwt_dead.py`) |
| Migrations | 45/45 applied; `push.py --verify` PASS=97 on prod | VERIFIED 2026-10-10 |
| Alerts | Suppression list (#315) and urgent-email coalescing (#316) live; unconfirmed in practice (no org has an alert preference enabled) | VERIFIED counts below |
| Schedulers | ingest-tick ENABLED; digest-daily and detector-sweep PAUSED | VERIFIED |

## Measured

| Claim | Number | Source |
|---|---|---|
| Held-out accuracy, full pipeline vs plain-prompted Llama 3.3 70B | 79.6% vs 78.3% (extraction accuracy is not a moat) | S21 brief; eval artifacts in `eval/results/` |
| Groq free limits on both orgs | 1,000 req/day and 8,000 tokens/min per model (20b, 120b, qwen3.8-27b) | VERIFIED response headers 2026-10-10 |
| Groq daily token pool | 200K/model/day | BELIEVED (Groq docs; not exposed in headers) |
| Failover latency (Groq to Groq, 120b) | 4.75 s for one 259-in/437-out call | one VERIFIED sample |
| Load test (mock LLM, one local uvicorn worker) | ~326 req/s at 0 ms provider latency, no error knee to c=128 | PR #323 (draft), `reports/load/s20-run.json`; DB path and Cloud Run unmeasured |
| Free OpenRouter models for evals | 1 of 4 probed answered; ~50 req/day cap BELIEVED | S20 probe; N1b frontier arm not possible free |
| Usage | 19 orgs, 1 with a notification email, 0 with an alert preference enabled, 0 orgs with an extraction in the last 7 days | VERIFIED read-only counts 2026-10-10 |

## Open, and what blocks each

| Item | Blocked on |
|---|---|
| Weekly digest (#317, ready, CLEAN) | GG merge (migrations 0003 touch gate 4); then apply 0003 before creating the weekly Scheduler job |
| Un-pausing digest-daily | Nothing is enabled to send (0 digest orgs); needs a customer to enable it, the Resend webhook secret and registration, and `DASHBOARD_URL` |
| Disable legacy Supabase keys (rotation step f) | GG, Supabase dashboard |
| OpenRouter key rotation | GG (old revisions carry it) |
| English labelling fixtures | Free OpenRouter yield too low; see the certification ADR |
| Tailwind 4 migration (#106) | Plan, before the braces allowlist expires 2026-12-31 |
| Load test #323 | Draft; reviewable size over 400 lines |
| claude-config hosted CI | GitHub Free private-repo minutes (resets about 2026-11-01); use `scripts/local_ci_attest.py` |
| Free-tier capacity of the dedicated org | Needs a burn to the first 429; not done to protect production |

## GG must do

See the end of the latest session report; this table is regenerated each session.
