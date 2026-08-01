-- Migration: backfill the CREATE TABLE this repo never actually had.
--
-- Found 2026-08-01 while building the pre-cutover ephemeral-Postgres CI job (P3):
-- applying every committed migration in order against a fresh vanilla Postgres
-- instance failed outright at 20260711000002_quota_requests_rls.sql with
-- "relation public.quota_requests does not exist" -- that migration's own header
-- already documented the gap ("public.quota_requests already exists in the live
-- database ... no prior migration file for it in this repo") but nothing had ever
-- backfilled it. Schema confirmed against the real production table directly
-- (information_schema.columns + pg_constraint, 2026-08-01), not guessed or solely
-- inferred from the later RLS/cascade migrations and
-- app/core/storage_pg.py::record_quota_request_pg()'s INSERT statement -- those
-- got every column name right except one: the timestamp column is
-- `requested_at`, not `created_at` like every other table in this schema (an
-- inconsistency worth knowing about, not silently "fixed" here). Table was empty
-- in production both times this was checked (2026-07-11, 2026-08-01) -- zero
-- data risk in this backfill, and CREATE TABLE IF NOT EXISTS is a no-op against
-- the real, already-existing table either way.

CREATE TABLE IF NOT EXISTS public.quota_requests (
  id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            uuid        NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  usage_at_request  integer     NOT NULL,
  quota_at_request  integer     NOT NULL,
  notes             text,
  requested_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_quota_requests_org_id ON public.quota_requests (org_id);
