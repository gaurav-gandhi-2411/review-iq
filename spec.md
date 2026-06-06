# Project Spec: review-iq — Phase 2.0a (Multi-Tenant SaaS on Cloud Run)

## Goal
Turn review-iq from a single-tenant demo (v0.1.3, SQLite, one shared `X-API-Key`, HF Spaces)
into a multi-tenant hosted SaaS that can onboard real paying clients. Each client (organization)
gets isolated data, their own API keys, and per-key quotas. The service runs on GCP Cloud Run
with hard cost controls so it cannot generate a surprise bill. The existing `/v1/*` API and the
HF Space demo stay live and unchanged as the legacy public demo.

This is the first sellable checkpoint: after 2.0a, review-iq is revenue-capable for SMB / DTC
clients as a hosted English review-intelligence API.

## Current state (existing project — do not break)
- **Live, working, must keep working:** `app/main.py` mounts 3 routers (`extract`, `query`,
  `dashboard`), Prometheus middleware, slowapi rate limiting, lifespan auto-migrate.
- **Core modules:** `app/core/{config,llm,sanitize,auth,storage,metrics,logging,schemas,prompt}.py`.
- **Storage:** `app/core/storage.py` — aiosqlite, WAL, manual migrations, SHA-256 idempotency
  cache, `extractions` + `batch_jobs` tables.
- **Auth today:** `app/core/auth.py` — single shared `X-API-Key` compared to `settings.api_key`.
- **LLM:** `app/core/llm.py` — Groq primary, Gemini fallback.
- **Eval:** `eval/runner.py` + `eval/fixtures/*.json` (25 fixtures present), 85% pass gate.
- **Tests:** `tests/unit/*` + `tests/integration/*`, 128 functions, 60% coverage gate.
- **CI:** `.github/workflows/{ci,deploy,eval}.yml`. `deploy.yml` pushes to HF Space.
- **The contract with existing code:** the `ReviewExtraction` schema in `app/core/schemas.py`
  and the `/v1` request/response shapes are a public contract. Do not change field names,
  types, or `/v1` behavior.

## Scope

### In scope (this iteration)
- Repository abstraction: `ReviewRepository` interface with **two** backends — SQLite (dev/test)
  and Postgres (prod). Same methods, swap by config.
- Postgres schema (Supabase): `organizations`, `users`, `organization_members`, `api_keys`,
  `usage_records`; add `org_id`, `tokens_in`, `tokens_out` to `extractions`. RLS on every
  tenant table.
- API-key auth middleware: `Authorization: Bearer riq_live_<32-hex>`; SHA-256 hashed at rest;
  store `key_prefix` for display; per-key `rate_limit_rpm` / `rate_limit_rpd` / `monthly_quota`;
  record usage in `usage_records` on every call.
- Tenancy services: CRUD for orgs / members / keys. Owner-only `/admin/*` endpoints behind
  HTTP Basic (single owner credential from Secret Manager) until a dashboard exists.
- `/v2/*` endpoints: tenant-scoped copies of `/v1/extract`, `/v1/extract/batch`,
  `/v1/extract/batch/{id}`, `/v1/reviews`, `/v1/insights`. Require Bearer key. Scope all reads
  and writes by `org_id`.
- **Privacy hardening:** make prod extraction Groq-only. Gate Gemini behind a `DEV_ONLY` config
  flag; it must never be invoked when a real org key is present. (Gemini free tier trains on
  inputs — unacceptable for client data.)
- Cloud Run deploy path: updated `Dockerfile`, `cloudbuild.yaml`, `deploy-cloudrun.yml`,
  secrets in Secret Manager.
- Cost-control IaC under `ops/budget-killswitch/` (Terraform): Pub/Sub budget topic +
  Cloud Function that disables billing on breach. Budget alerts at $0.50 / $1 / $5 / $10.
- Tests: v1 still works; v2 requires auth; **cross-tenant isolation** (org A cannot read org B);
  **quota enforcement** (key with quota=10 returns 429 on the 11th call).
- README v2 section + `SECURITY.md` (PI defense, PII handling, RLS).

### Out of scope (do not build)
- Python / JS SDKs (Phase 2.5).
- Browser extension, embed widget (Phase 3.0).
- Hinglish / Hindi / Tamil language work (Phase 2.0b — separate spec).
- CSV bulk ingestion + self-serve sign-up + landing page (Phase 2.0c — separate spec).
- Any billing / payments / Stripe code.
- Removing or rewriting the `/v1` endpoints or the HF Space deploy.

## Tech stack
- Python 3.11, FastAPI ≥0.115, Pydantic v2, uvicorn[standard] (unchanged).
- Postgres via `asyncpg` (prod); `aiosqlite` retained for dev/test.
- Migrations: `alembic` (or Supabase migration files — pick one, state it, be consistent).
- `passlib`/`hashlib` for API-key hashing (SHA-256 is fine; no plaintext keys at rest).
- GCP: Cloud Run, Artifact Registry, Secret Manager, Cloud Billing budgets. Terraform for the
  kill-switch.
- uv (package manager), ruff (line-length 100), mypy (strict on `app/`), pytest + pytest-asyncio.

## Architecture
```
app/
  main.py                 # mount v1 + v2 routers; unchanged v1 behavior
  api/
    v1/                    # MOVE existing extract/query/dashboard here, behavior unchanged
    v2/                    # NEW: tenant-scoped extract, query
  auth/
    keys.py                # NEW: parse/hash/lookup riq_live_ keys, quota check, usage record
    admin.py               # NEW: HTTP Basic owner auth for /admin/*
  tenancy/
    service.py             # NEW: org/user/member/key CRUD
  core/
    repository.py          # NEW: ReviewRepository interface
    repository_sqlite.py   # NEW: SQLite impl (wraps current storage.py logic)
    repository_postgres.py # NEW: asyncpg impl
    llm.py                 # EDIT: Groq-only prod path; Gemini behind DEV_ONLY flag
    ...                    # config/schemas/sanitize/metrics/logging unchanged in contract
ops/
  budget-killswitch/       # NEW: Terraform — Pub/Sub + Cloud Function
  runbooks/                # NEW: monthly-cost-check.md
cloudbuild.yaml            # NEW
.github/workflows/
  deploy-cloudrun.yml      # NEW (deploy.yml for HF stays)
SECURITY.md                # NEW
```

## Data model (Postgres, v2.0a)
```sql
organizations (id, name, slug, plan, created_at)
users (id, email, name, created_at)
organization_members (org_id, user_id, role)            -- owner|admin|member
api_keys (id, org_id, key_hash, key_prefix, name,
          rate_limit_rpm, rate_limit_rpd, monthly_quota,
          created_at, last_used_at, revoked_at)
extractions (id, org_id, api_key_id, input_hash, review_text_redacted,
             output_json, model, prompt_version, schema_version,
             extracted_at, latency_ms, tokens_in, tokens_out)
             INDEX (org_id, extracted_at DESC), INDEX (org_id, input_hash)
usage_records (org_id, api_key_id, date, extractions_count,
               tokens_in_total, tokens_out_total)        -- PK (org_id, api_key_id, date)
```
RLS policies on `organizations`, `api_keys`, `extractions`, `usage_records`: a tenant role can
only read/write rows where `org_id` matches its claim. Isolation must hold even if app code is buggy.

## Verification commands
```yaml
- name: tests
  cmd: uv run pytest -v
  required: true
- name: lint
  cmd: uv run ruff check .
  required: true
- name: format
  cmd: uv run ruff format --check .
  required: true
- name: types
  cmd: uv run mypy app
  required: true
```

## Subagent usage rules
- Use `executor` for any pass that writes or edits files.
- Use `verifier` for running tests / lint / format / types.
- The orchestrator does NOT write code — always delegates.

## Escalation rules (orchestrator must ask before doing)
- **Ask before ANY `gcloud` / `gsutil` / billing command targeting `review-iq-prod`** — every
  such action is individually approved by the user. Never auto-run.
- Cloud Run cost controls (`--max-instances=2`, `--min-instances=0`, budget alerts, kill-switch)
  MUST be deployed and verified BEFORE any production traffic. Escalate if asked to deploy a
  service before the kill-switch exists.
- GCP project creation, billing-account linking, Supabase project creation, and OAuth/Auth
  config are **dashboard-only / user actions** — surface the exact steps for the user; do not
  attempt them via CLI.
- Ask before installing any dependency not listed in "Tech stack."
- Ask before changing any `/v1` request/response shape or any field in `ReviewExtraction`.
- Ask before a single executor pass would touch more than 8 files.
- Ask if verification fails 3 times in a row on the same check.
- Ask before committing anything that looks like a secret; secrets go to Secret Manager +
  `.env.example` only.

## Hard rules (existing project)
- Do NOT modify `/v1` endpoint behavior or the HF Space `deploy.yml`.
- Do NOT remove the SQLite backend — tests depend on it; it becomes the dev/test repository impl.
- Do NOT change `ReviewExtraction` / `ReviewExtractionLLMOutput` field names or types.
- Do NOT invoke Gemini on any path reachable by a real org key.
- Run the full existing test suite after every executor pass; escalate if any existing test fails.
- No plaintext API keys at rest — hash before store, ever.

## Budget
- Soft target: 2–3 CC sessions (this is genuinely multi-session work).
- Hard cap: stop and escalate after 25 executor invocations.
- Cost check: orchestrator runs `/cost` at the midpoint and reports.

## Success criteria (orchestrator verifies ALL before declaring done)
- [ ] `ReviewRepository` interface with SQLite + Postgres impls; tests green on SQLite.
- [ ] Postgres schema applied; RLS policies present on all tenant tables.
- [ ] `/v2/extract` requires a valid `riq_live_` Bearer key; rejects missing/invalid with 401.
- [ ] Cross-tenant isolation test passes: org A's key cannot read org B's extractions/reviews.
- [ ] Quota test passes: a key with `monthly_quota=10` returns 429 on the 11th extraction.
- [ ] Usage recorded in `usage_records` for every v2 call (count + tokens).
- [ ] Gemini unreachable on the org-key path; `DEV_ONLY` flag gates it; test asserts this.
- [ ] All `/v1` endpoints behave identically to v0.1.3 (regression tests green).
- [ ] Eval runner ≥85% pass (no regression).
- [ ] Cloud Run service responds on its `*.run.app` URL; cost controls + kill-switch deployed.
- [ ] Kill-switch tested via a simulated budget breach; billing-disable path confirmed.
- [ ] $0 spend confirmed on `review-iq-prod`; `ops/runbooks/monthly-cost-check.md` executed once.
- [ ] `SECURITY.md` added; README v2 section added.
- [ ] `v0.2.0` tagged.

## Build order (recommended; orchestrator may adjust)
1. Cost controls FIRST: `ops/budget-killswitch/` Terraform + budget alerts (user runs gcloud/console steps; orchestrator authors IaC and the runbook).
2. `ReviewRepository` interface; refactor current `storage.py` logic into `repository_sqlite.py`; keep all tests green.
3. Postgres schema + RLS migrations; `repository_postgres.py`; config switch dev↔prod.
4. API-key auth middleware (`app/auth/keys.py`): parse, hash, lookup, quota, usage record.
5. Tenancy services + owner-only `/admin/*` (HTTP Basic).
6. Move existing routers under `app/api/v1/` (behavior unchanged); add `app/api/v2/` tenant-scoped.
7. LLM privacy hardening: Groq-only prod path, Gemini behind `DEV_ONLY`.
8. Cloud Run: Dockerfile, `cloudbuild.yaml`, `deploy-cloudrun.yml`, Secret Manager (user approves each gcloud action).
9. Isolation + quota + v1-regression tests; eval re-run.
10. `SECURITY.md`, README v2 section, `v0.2.0` tag.
