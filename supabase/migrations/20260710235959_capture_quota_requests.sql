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

-- ---------------------------------------------------------------------------
-- Postconditions (Session 15d) -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py. Each holds against the FINAL
-- schema state (a later migration must not undo it), in the CI build and in production.
-- ---------------------------------------------------------------------------
-- @postcondition: quota_requests_columns_and_key
-- SQL: SELECT (SELECT count(*) FROM pg_attribute a WHERE a.attrelid =
-- SQL: 'public.quota_requests'::regclass AND a.attnum > 0 AND NOT a.attisdropped AND (a.attname,
-- SQL: format_type(a.atttypid, a.atttypmod), a.attnotnull) IN (('id', 'uuid', true), ('org_id',
-- SQL: 'uuid', true), ('usage_at_request', 'integer', true), ('quota_at_request', 'integer', true),
-- SQL: ('notes', 'text', false), ('requested_at', 'timestamp with time zone', true))) = 6 AND
-- SQL: EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conrelid = 'public.quota_requests'::regclass
-- SQL: AND c.contype = 'p' AND pg_get_constraintdef(c.oid) = 'PRIMARY KEY (id)')
