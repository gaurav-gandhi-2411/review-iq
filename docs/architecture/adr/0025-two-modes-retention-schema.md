# ADR 0025: Two modes — stateless (default) and retained, with a purge job and on-demand purge

## Context

D1 (Session 12): stateless is the default (`POST /v2/extract` persists nothing), retained is
opt-in per org with a customer-chosen window (30/90 days), and it powers the dashboard,
`GET /reviews`, `GET /insights`, and trend aggregation. ADR 0024 (P1) established that
`save_extraction_pg` is the single choke point every extraction path funnels through, and named
exactly what needed to change. This ADR is that change.

## Schema change — STOP for GG, not applied

`supabase/migrations/20260912000001_organizations_retention_mode.sql`:

```sql
ALTER TABLE public.organizations
    ADD COLUMN IF NOT EXISTS retention_mode TEXT NOT NULL DEFAULT 'stateless'
        CHECK (retention_mode IN ('stateless', 'retained'));

ALTER TABLE public.organizations
    ADD COLUMN IF NOT EXISTS retention_days INTEGER
        CHECK (retention_days IS NULL OR retention_days IN (30, 90));

-- + organizations_retention_consistent CHECK (stateless <=> NULL days, retained <=> NOT NULL days)
-- + public.list_orgs_with_retained_mode(): narrow SECURITY DEFINER cross-org lister
--   (org_id, retention_days only), same pattern as list_orgs_with_daily_digest.
```

**Hits the table-ownership wall** (Session 11 H3d): `public.organizations` is owned by
`postgres`, not `review_iq_migrator`. Apply via the Supabase SQL Editor as `postgres`, same
mechanism as Session 11's `extraction_costs` migration, or after GG's parallel ownership-transfer
work (P7c) lands.

**Verified safe to default every existing org to `stateless`**: queried every row in
`organizations` (18) and `api_keys` (11) directly before writing this migration — every single
one is a test/demo/dev artifact from prior sessions, no real paying customer exists yet (D5).
This will not hold true forever; re-verify before ever reusing this reasoning.

## Implementation (built on this branch, blocked on the migration landing)

**`ApiKeyContext`** (`app/auth/api_key.py`) gains `retention_mode: str = "stateless"` and
`retention_days: int | None = None`, both defaulting to the safe (non-persisting) value so every
pre-existing construction site keeps compiling without modification. `require_api_key`'s query
now joins `organizations` (`FOR UPDATE OF ak` only — the org row is read-only here and must not
be lock-contended by concurrent requests against the org's other keys).

**Every other `ApiKeyContext` construction site fetches the real value, never assumes a
default** — this was the actual hard part, not the happy path. Three sites build their own
context directly rather than going through `require_api_key`, and all three were audited:

- `app/auth/session.py` (BFF dashboard auth, two functions) — added `get_org_retention_pg` calls.
- `app/api/webhooks/shopify.py` / `app/api/webhooks/google.py` — same. Without this fix, a
  Shopify- or Google-connected **retained**-mode org's auto-ingested reviews would have silently
  defaulted to the dataclass's `"stateless"` default and never persisted — a silent product
  regression for exactly the customers who most want auto-ingestion to populate their dashboard.
  Caught by tracing every construction site, not by a test failure.
- `app/core/ingest_worker.py` (CSV-drain per-row context) — same, plus re-checked per row rather
  than assumed from upload time, since the queue can outlive a mode change between upload and
  drain.

**`_run_extraction_v2`** (`app/api/v2/extract.py`) gates on `ctx.retention_mode != "retained"`:
stateless skips both the `get_by_hash_pg` cache lookup (nothing persisted to find, and a mode
switch from retained→stateless should stop surfacing old cache hits, not quietly contradict the
switch) and `save_extraction_pg`. `extraction_id` becomes `None` for stateless calls, matching
the existing pattern for the concurrent-insert race case — `record_extraction_cost_pg`'s FK is
already nullable for exactly this reason.

## P2e — CSV ingest requires retained mode

`POST /v2/ingest/csv` now rejects a stateless-mode org with `409 Conflict` before any file
parsing. This is the honest answer, not a workaround: `batch_job_rows.text` stages review text
durably (surviving a Cloud Run restart mid-drain is the entire reason that table exists — see
`app/core/ingest_worker.py`'s module docstring), and there is no way to make that staging
stateless without either losing the durability guarantee bulk ingestion depends on, or building a
second, materially more complex staging mechanism. `POST /v2/extract` (one review at a time)
remains available in stateless mode as the alternative.

## P2c — purge job and on-demand endpoint

- **Scheduled**: `POST /internal/retention/purge`, token-protected (`X-Retention-Purge-Trigger-
  Token`, same shared-secret pattern as `/internal/digest/run`), intended for a daily Cloud
  Scheduler cadence (retention windows are 30/90 days, not hours — daily is more than enough
  granularity). `app/core/retention.py::purge_expired_extractions` lists every retained-mode org
  via the new SECURITY DEFINER function, computes each org's own cutoff
  (`now() - retention_days`), and deletes via `_set_tenant()`-scoped per-org queries — no
  BYPASSRLS, no cross-tenant risk, one org's failure never stops the sweep for the rest.
  **Not yet wired to an actual Cloud Scheduler job** — the endpoint is ready and tested, but
  creating the Scheduler entry itself (`gcloud scheduler jobs create http ...`) is a small
  separate infra step, reported here rather than executed, given this whole feature is blocked on
  the schema landing first anyway.
- **On-demand**: `POST /v2/purge` (org-authenticated, any retention mode) deletes every stored
  extraction for the calling org immediately. No confirmation step — this is an API, not a UI;
  clients should implement their own confirmation.

## P2d — the product-claim test

`tests/integration/test_retention_modes.py::test_stateless_extraction_leaves_no_trace_in_any_table_or_log`
is the test the brief asked for: a sentinel string, mocked LLM output that echoes it verbatim
into `product` (mirroring ADR 0024's live finding), a real `/v2/extract` call, then an **unscoped
scan of every text/jsonb column in every table** plus a `caplog`-captured check across every log
record emitted during the call. Same methodology as ADR 0024's live audit — a scan limited to
the columns this test expects to be clean cannot prove the negative. A control test
(`test_retained_mode_extraction_does_persist`) proves the gate actually distinguishes modes,
not merely that persistence is broken for everyone. Runs in `pre-cutover-verification.yml`/
`bypassrls-container-check.yml`'s ephemeral, fully-migrated Postgres — the only environment where
this migration currently exists at all.

## Consequences

- This PR cannot merge to `main` until the migration lands (the integration test requires the
  new columns/function to exist, and `_run_extraction_v2`'s behavior change is meaningless
  without them) — left as DRAFT per the autonomy contract's schema-migration stop rule, same
  pattern as Session 11's PR #154.
- Every pre-existing unit test that exercises `_run_extraction_v2` via a bare `ApiKeyContext`
  now must set `retention_mode="retained"` explicitly to keep testing the persist-by-default
  behavior it was written against — updated in this PR (`test_v2_extract.py`, `test_v2_ingest.py`,
  `test_ingest_worker.py`, `test_ratelimit.py`, `test_auth_api_key.py`, `test_bff_session.py`).
  This is deliberate, not incidental churn: it makes explicit, in every test, which mode is being
  exercised.
- `app/core/prompts/**` is untouched — this is a data-retention change, not a prompt change.

## Alternatives considered

- **Default new orgs to stateless but leave existing orgs untouched (retained implicitly).**
  Rejected: verified no real customer exists yet, so there is no "existing org" to protect from a
  behavior change — the simpler, uniform default is strictly better with nothing to lose.
- **Make CSV ingest "best-effort stateless"** (process rows without ever writing
  `batch_job_rows.text`, holding them in memory only). Rejected: this removes the exact
  durability guarantee (surviving a Cloud Run restart mid-drain) that table exists for, turning a
  500-row upload into a single point of failure for the whole batch. Reported as retained-only
  instead of silently degrading a feature's reliability to fake a stateless label.
- **Skip the on-demand purge endpoint, ship only the scheduled job.** Rejected: a customer asking
  "delete my data now" and being told "wait up to 30/90 days" is not a credible trust story for a
  product whose entire pitch includes data control.
