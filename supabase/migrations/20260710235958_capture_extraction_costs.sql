-- Migration: capture public.extraction_costs, an out-of-band table.
--
-- CAPTURE, NOT CHANGE. This table already exists in production, created outside the
-- migration workflow at some earlier point -- no prior migration file for it in this
-- repo, discovered 2026-08-16 (Item 141) via a full live-schema-vs-migrations diff, the
-- same audit that found the already-documented public.quota_requests gap in
-- 20260711000002_quota_requests_rls.sql. Every column, type, default, constraint,
-- index, RLS policy, and grant below was read directly from the live database via
-- information_schema/pg_catalog queries (schema only, zero rows selected) and is
-- reproduced exactly -- this migration is a faithful snapshot of what's already
-- deployed, not a design decision, and does NOT need to be applied to production
-- (it already matches). Its purpose is making the repo's migration history match
-- reality, and unblocking a from-scratch schema replay (e.g. a throwaway CI Postgres
-- container, Item 135/142) that currently fails because this table is missing.
--
-- Numbered to slot before 20260711000001_alert_event_types.sql -- extraction_costs
-- predates that migration in the live database (created before 2026-07-11, the date
-- the sibling quota_requests gap was first documented).
--
-- Note for GG, not addressed here: live grants on `authenticated` include DELETE,
-- REFERENCES, TRIGGER, TRUNCATE, and UPDATE in addition to INSERT/SELECT -- broader
-- than the "write-once" pattern used elsewhere in this schema (e.g. quota_requests
-- grants only INSERT+SELECT). Captured exactly as found; narrowing it is a separate
-- decision, not made here.

CREATE TABLE IF NOT EXISTS public.extraction_costs (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
  extraction_id     uuid REFERENCES public.extractions(id) ON DELETE SET NULL,
  provider          text NOT NULL,
  model             text NOT NULL,
  tier              text NOT NULL,
  language          text,
  tokens_in         integer NOT NULL DEFAULT 0 CHECK (tokens_in >= 0),
  tokens_out        integer NOT NULL DEFAULT 0 CHECK (tokens_out >= 0),
  cost_usd          numeric NOT NULL CHECK (cost_usd >= 0),
  cost_inr          numeric NOT NULL CHECK (cost_inr >= 0),
  created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_extraction_costs_org_created_at
  ON public.extraction_costs USING btree (org_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_extraction_costs_language_tier
  ON public.extraction_costs USING btree (language, tier);

-- Live grants, captured exactly (see note above on breadth).
REVOKE ALL ON public.extraction_costs FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
  ON public.extraction_costs TO authenticated;

ALTER TABLE public.extraction_costs ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  DROP POLICY IF EXISTS "extraction_costs_authenticated_all" ON public.extraction_costs;
  DROP POLICY IF EXISTS "extraction_costs_anon_deny"         ON public.extraction_costs;
END $$;

CREATE POLICY "extraction_costs_authenticated_all" ON public.extraction_costs
  FOR ALL TO authenticated
  USING     (org_id = public.current_org_id())
  WITH CHECK (org_id = public.current_org_id());

CREATE POLICY "extraction_costs_anon_deny" ON public.extraction_costs
  FOR ALL TO anon USING (false);

-- ---------------------------------------------------------------------------
-- Postconditions (Session 15d) -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py. Each holds against the FINAL
-- schema state (a later migration must not undo it), in the CI build and in production.
-- ---------------------------------------------------------------------------
-- @postcondition: extraction_costs_columns
-- SQL: SELECT (SELECT count(*) FROM pg_attribute a WHERE a.attrelid =
-- SQL: 'public.extraction_costs'::regclass AND a.attnum > 0 AND NOT a.attisdropped AND (a.attname,
-- SQL: format_type(a.atttypid, a.atttypmod), a.attnotnull) IN (('id', 'uuid', true),
-- SQL: ('extraction_id', 'uuid', false), ('provider', 'text', true), ('model', 'text', true),
-- SQL: ('tier', 'text', true), ('language', 'text', false), ('tokens_in', 'integer', true),
-- SQL: ('tokens_out', 'integer', true), ('created_at', 'timestamp with time zone', true))) = 9 AND
-- SQL: (SELECT count(*) FROM pg_attribute a WHERE a.attrelid = 'public.extraction_costs'::regclass
-- SQL: AND a.attnum > 0 AND NOT a.attisdropped AND a.attnotnull AND a.attname IN ('cost_usd',
-- SQL: 'cost_inr')) = 2 AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid =
-- SQL: 'public.extraction_costs'::regclass AND a.attname = 'org_id' AND NOT a.attisdropped AND
-- SQL: format_type(a.atttypid, a.atttypmod) = 'uuid')

-- @postcondition: extraction_costs_indexes
-- SQL: SELECT EXISTS (SELECT 1 FROM pg_index i WHERE i.indexrelid =
-- SQL: to_regclass('public.idx_extraction_costs_org_created_at') AND i.indrelid =
-- SQL: 'public.extraction_costs'::regclass) AND EXISTS (SELECT 1 FROM pg_index i WHERE i.indexrelid
-- SQL: = to_regclass('public.idx_extraction_costs_language_tier') AND i.indrelid =
-- SQL: 'public.extraction_costs'::regclass)

-- @postcondition: extraction_costs_rls_and_policies
-- SQL: SELECT (SELECT count(*) = 1 AND bool_and(c.relrowsecurity) FROM pg_class c WHERE c.oid IN
-- SQL: ('public.extraction_costs'::regclass)) AND (SELECT count(*) FROM pg_policies p WHERE
-- SQL: p.schemaname = 'public' AND (p.tablename, p.policyname, p.cmd, p.roles::text) IN
-- SQL: (('extraction_costs', 'extraction_costs_authenticated_all', 'ALL', '{authenticated}'),
-- SQL: ('extraction_costs', 'extraction_costs_anon_deny', 'ALL', '{anon}'))) = 2 AND (SELECT
-- SQL: count(*) FROM pg_policies p WHERE p.schemaname = 'public' AND p.policyname IN
-- SQL: ('extraction_costs_authenticated_all') AND p.qual LIKE '%current_org_id()%' AND p.with_check
-- SQL: LIKE '%current_org_id()%') = 1 AND (SELECT count(*) FROM pg_policies p WHERE p.schemaname =
-- SQL: 'public' AND p.policyname IN ('extraction_costs_anon_deny') AND p.qual = 'false') = 1
