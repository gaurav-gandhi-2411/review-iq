# Project Spec: review-iq — Phase 2.0c (Ingestion + Self-Serve → Close-Out)

## Goal
Make review-iq a product a stranger in India can adopt without talking to you: sign up with an
email, get a `riq_live_` key, push a marketplace CSV export of reviews (English, Hinglish, or
Hindi), and pull back structured insights — with a public landing page whose accuracy table is
the sales pitch. This is the final phase. When it ships, the project is closed as a sellable
product. Everything beyond it (SDKs, browser extension, embed widget, payments) is growth, not
close-out.

## Current state (existing project — do not break)
- `main` is at `v0.3.1` (commit `ba7e7b8`). Phases 2.0a + 2.0b are complete and live.
- Multi-tenant core (done, must keep working): `app/auth/{api_key,keygen,admin}.py`
  (argon2id `riq_live_<32hex>` keys, quota, usage), `app/api/v2/{extract,reviews}.py`
  (tenant-scoped), `app/api/admin.py` (owner-only), `app/core/storage_pg.py` (Postgres),
  `app/core/storage.py` (SQLite dev/test), RLS migrations in `supabase/migrations/`.
- Language (done): `app/core/language.py` + `app/core/prompts/{en,hi,hi_en}.py`, fixtures in
  `eval/fixtures/{,hi/,hi-en/}`, multi-language eval runner with 80%-per-language gate.
- Batch pipeline (exists): `BatchJob` model + `batch_jobs` table; the v2 batch task path lives
  in `app/api/v2/extract.py` and is currently 72% covered (integration-only) — this is the
  load-bearing path for CSV ingestion and must be hardened first.
- Privacy gate (done): v2/org-key path is Groq-only; `enable_gemini_fallback` defaults False.
- Deployed: Cloud Run (`review-iq-prod`), kill-switch live, console-verified healthy.
- The contract: do not change `ReviewExtraction` field names/types, `/v1` behavior, the
  `riq_live_` key format, or RLS policies.

## Scope

### In scope (this iteration)
- CSV bulk ingestion (v2, tenant-scoped):
  - `POST /v2/ingest/csv` — multipart upload; caller names the review-text column (param
    `text_column`, default tries `review_text`/`review`/`comment`/`text`) and optional
    `product_column`; validates headers; enqueues a `BatchJob`; returns `job_id`.
  - `GET /v2/ingest/{job_id}` — status + progress (reuse existing batch status).
  - `GET /v2/ingest/{job_id}/result?format=csv|json` — download original rows joined with
    extracted fields.
  - Caps (cost guard): free tier <= 500 rows/upload and <= 5 MB/file; reject oversize with 413.
    Stream-parse (stdlib `csv`), never load the whole file into memory.
- Self-serve onboarding:
  - Supabase Auth magic-link sign-up (email only; Supabase sends the email).
  - On first verified login: create an `organization`, issue one `riq_live_` key (shown once),
    set `monthly_quota = 100` (free tier).
  - Minimal authenticated account page: show key prefix + reveal-once, current usage
    (`N / 100` this month), and a regenerate-key action. NOT a full analytics dashboard.
- Landing page + docs (static, separate host):
  - Hero, one-line value prop (reviews -> structured insight, English + Hinglish + Hindi).
  - Live demo box: paste a review -> calls the public v1 demo endpoint (no key, rate-limited)
    -> shows the JSON. Zero-friction; do not require sign-up to try.
  - Real per-language accuracy table (en/hi/hi-en, current eval numbers) + honest known-gap note.
  - Quickstart: `curl` + Python snippets against `/v2/extract`.
  - "Self-host (MIT) or use hosted" section; transparent "how we make money" line.
  - `/docs` (mkdocs or plain HTML): endpoints, auth, CSV format, limits.
- hi-en prompt refinement (bounded, one pass): improve the Hinglish few-shot examples in
  `app/core/prompts/hi_en.py` targeting the sarcasm/ambiguity fixtures (hi-en-010/012/014/015);
  re-run eval. Accept whatever it lands at as long as every bucket stays >= 80%. Do NOT chase a
  specific higher number or iterate more than one attempt — escalate if it regresses any bucket.

### Out of scope (do not build)
- Python/JS SDKs, browser extension, embed widget (growth backlog).
- Stripe/payments/billing code — first clients are invoiced manually.
- A full per-tenant analytics dashboard (the account page is intentionally minimal).
- Marketplace API connectors (Amazon SP-API, Flipkart) — CSV export is the ingestion path for now.
- Any change to `/v1`, `/v2/extract`, `/v2/reviews`, RLS, or the key format.

## Tech stack
- Existing stack unchanged (Python 3.11, FastAPI, Pydantic v2, asyncpg/aiosqlite, Groq, uv,
  ruff, mypy strict, pytest).
- `python-multipart` for file upload (add only if not already present — confirm first).
- CSV: stdlib `csv` only (no pandas — avoid the heavy dep).
- Auth: Supabase Auth (already in use) — magic link; no new auth library.
- Landing/docs: static site on Cloudflare Pages (project creation is a user/dashboard action).

## Architecture
```
app/
  api/v2/
    ingest.py            # NEW: CSV upload / status / result (tenant-scoped, reuses BatchJob)
  api/
    account.py           # NEW: minimal authenticated account page (key, usage, regenerate)
  auth/
    signup.py            # NEW: Supabase magic-link verify -> org + riq_live_ key issuance
  core/
    csv_ingest.py        # NEW: streaming CSV parse + column mapping + result join
    prompts/hi_en.py     # EDIT: refined Hinglish few-shot examples (one bounded pass)
site/                    # NEW: static landing page + /docs (Cloudflare Pages)
  index.html
  docs/
```

## Data model (additions only)
- No new tables required. Reuse `organizations`, `api_keys`, `usage_records`, `batch_jobs`.
- If a job needs to remember its source columns for the result join, add nullable
  `source_columns TEXT` (JSON) to `batch_jobs` via a new migration — escalate before adding.

## Verification commands
```yaml
- name: tests
  cmd: uv run pytest -v
  required: true
- name: integration
  cmd: uv run pytest -v -m integration
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
- name: eval
  cmd: uv run python eval/runner.py
  required: true
```

## Subagent usage rules
- `executor` writes/edits files; `verifier` runs the commands above. Orchestrator delegates all code.

## Escalation rules (orchestrator must ask before doing)
- Ask before ANY `gcloud`/billing command targeting `review-iq-prod` — per-action approval.
- Supabase Auth configuration (magic-link, redirect URLs) and Cloudflare Pages project creation
  are dashboard-only / user actions — surface exact steps; do not attempt via CLI.
- Free-tier caps (100 extractions/mo, 500 rows/upload, 5 MB/file) MUST be enforced before the
  signup or ingest endpoints are exposed — escalate if asked to ship either without caps.
- Ask before adding any dependency beyond "Tech stack" (including pandas — don't).
- Ask before adding the `source_columns` column or any migration.
- Pricing numbers, free-tier limits beyond the above, ToS/Privacy/DPA copy -> the USER provides;
  leave placeholders, do not invent legal text or prices.
- Ask if a single executor pass would touch more than 8 files, or if verification fails 3x on the
  same check.
- hi-en refinement: one attempt only; escalate rather than iterating if any bucket drops below 80%.

## Hard rules
- Do NOT modify `/v1`, `/v2/extract`, `/v2/reviews`, `app/api/admin.py`, RLS policies, the
  `riq_live_` format, or `ReviewExtraction` fields.
- Do NOT invoke Gemini on any org-key path.
- Do NOT load entire CSVs into memory — stream.
- No plaintext keys at rest, ever.
- Run the full suite after every executor pass; escalate if any existing test fails.

## Budget
- Soft target: 1-2 CC sessions.
- Hard cap: stop and escalate after 20 executor invocations.
- Cost check: `/cost` at midpoint.

## Success criteria (orchestrator verifies ALL before declaring done)
- [ ] v2 batch/ingest path coverage >= 80% (closes the 72% gap), via real integration tests.
- [ ] CSV round-trip: upload a sample marketplace CSV -> poll -> download joined results (CSV+JSON),
      tenant-scoped, with row/size caps enforced (413 on oversize).
- [ ] Self-serve flow end-to-end: email magic link -> verified login -> org created -> `riq_live_`
      key issued (shown once) -> first `/v2/extract` succeeds. No manual step from the user.
- [ ] New org defaults to `monthly_quota = 100`; the existing quota->429 path applies.
- [ ] Account page: shows key prefix, usage `N/100`, regenerate works (old key revoked).
- [ ] hi-en prompt refinement pass run; eval re-run; all buckets >= 80%; per-language table updated.
- [ ] Landing page live: hero, working keyless live demo, real accuracy table, curl+Python
      quickstart, self-host/hosted + transparency line. `/docs` published.
- [ ] README + SECURITY.md updated for ingestion + signup; full suite + eval green.
- [ ] `v0.4.0` tagged. Project closed.

## Build order (recommended; orchestrator may adjust)
1. Harden the v2 batch path with integration tests to >= 80% coverage (foundation for ingestion).
2. `core/csv_ingest.py` + `api/v2/ingest.py` (upload/status/result) with caps + streaming.
3. `auth/signup.py` (Supabase magic-link -> org + key) + `api/account.py` (key/usage/regenerate).
   User configures Supabase Auth in the dashboard.
4. hi-en prompt refinement (one pass) + eval re-run.
5. `site/` landing + `/docs`; wire live demo to the public v1 demo endpoint. User creates the
   Cloudflare Pages project.
6. README/SECURITY updates, full verification, `v0.4.0` tag.
