-- Migration: capture public.quota_requests, an out-of-band table.
--
-- CAPTURE, NOT CHANGE. This table's absence from the migration history was already
-- documented in 20260711000002_quota_requests_rls.sql's own header comment ("public
-- .quota_requests already exists in the live database -- created outside the migration
-- workflow at some earlier point -- no prior migration file for it in this repo"),
-- confirmed 2026-08-16 (Item 141) via direct live-schema introspection: every column,
-- type, default, and constraint below was read from information_schema/pg_catalog
-- (schema only, zero rows selected) and is reproduced exactly.
--
-- Deliberately recreates the table's ORIGINAL dangerous-defaults state -- anon and
-- authenticated both granted INSERT/SELECT/UPDATE/DELETE/TRUNCATE, RLS not enabled --
-- exactly as 20260711000002_quota_requests_rls.sql's own comment describes finding it,
-- rather than jumping straight to the fixed end-state. That migration already runs
-- immediately after this one (numbered to slot directly before it) and performs the
-- real fix (REVOKE the dangerous grants, ENABLE RLS, add the correct policies) -- this
-- preserves the real history the codebase already tells, instead of silently
-- rewriting it. Does NOT need to be applied to production (already matches); this
-- unblocks a from-scratch schema replay (Item 135/142) that currently fails because
-- this table is missing.

CREATE TABLE IF NOT EXISTS public.quota_requests (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
  usage_at_request    integer NOT NULL,
  quota_at_request    integer NOT NULL,
  notes               text,
  requested_at        timestamptz NOT NULL DEFAULT now()
);

-- Supabase's default PostgREST auto-grants for a dashboard-created table -- this is
-- exactly the dangerous state 20260711000002_quota_requests_rls.sql was written to fix.
GRANT INSERT, SELECT, UPDATE, DELETE, TRUNCATE ON public.quota_requests TO anon;
GRANT INSERT, SELECT, UPDATE, DELETE, TRUNCATE ON public.quota_requests TO authenticated;
