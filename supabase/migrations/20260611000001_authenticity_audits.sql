-- Migration: authenticity_audits table for IS 19000:2022 compliance audit trail.
-- Pattern matches existing tenant tables: same current_org_id() helper, same
-- USING + WITH CHECK on authenticated, anon deny.
--
-- NOTE: public.current_org_id() is already defined in 20260510000002_rls_policies.sql.
-- This migration does NOT redefine it.

-- ---------------------------------------------------------------------------
-- Table
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.authenticity_audits (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID NOT NULL,
    review_hash TEXT NOT NULL,
    score       REAL NOT NULL,
    label       TEXT NOT NULL,
    flags       TEXT NOT NULL DEFAULT '[]',   -- JSON array
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_authenticity_audits_org_created
    ON public.authenticity_audits (org_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Row-Level Security
-- ---------------------------------------------------------------------------
ALTER TABLE public.authenticity_audits ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  DROP POLICY IF EXISTS "authenticity_audits_authenticated_all" ON public.authenticity_audits;
  DROP POLICY IF EXISTS "authenticity_audits_anon_deny"         ON public.authenticity_audits;
END $$;

CREATE POLICY "authenticity_audits_authenticated_all" ON public.authenticity_audits
  FOR ALL TO authenticated
  USING     (org_id = public.current_org_id())
  WITH CHECK (org_id = public.current_org_id());

CREATE POLICY "authenticity_audits_anon_deny" ON public.authenticity_audits
  FOR ALL TO anon USING (false);

-- ---------------------------------------------------------------------------
-- Postconditions (Session 15d) -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py. Each holds against the FINAL
-- schema state (a later migration must not undo it), in the CI build and in production.
-- ---------------------------------------------------------------------------
-- @postcondition: authenticity_audits_columns_and_index
-- SQL: SELECT (SELECT count(*) FROM pg_attribute a WHERE a.attrelid =
-- SQL: 'public.authenticity_audits'::regclass AND a.attnum > 0 AND NOT a.attisdropped AND
-- SQL: (a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull) IN (('id', 'uuid', true),
-- SQL: ('org_id', 'uuid', true), ('review_hash', 'text', true), ('score', 'real', true), ('label',
-- SQL: 'text', true), ('flags', 'text', true), ('created_at', 'timestamp with time zone', true))) =
-- SQL: 7 AND EXISTS (SELECT 1 FROM pg_index i WHERE i.indexrelid =
-- SQL: to_regclass('public.idx_authenticity_audits_org_created') AND i.indrelid =
-- SQL: 'public.authenticity_audits'::regclass)

-- @postcondition: authenticity_audits_rls_and_policies
-- SQL: SELECT (SELECT count(*) = 1 AND bool_and(c.relrowsecurity) FROM pg_class c WHERE c.oid IN
-- SQL: ('public.authenticity_audits'::regclass)) AND (SELECT count(*) FROM pg_policies p WHERE
-- SQL: p.schemaname = 'public' AND (p.tablename, p.policyname, p.cmd, p.roles::text) IN
-- SQL: (('authenticity_audits', 'authenticity_audits_authenticated_all', 'ALL', '{authenticated}'),
-- SQL: ('authenticity_audits', 'authenticity_audits_anon_deny', 'ALL', '{anon}'))) = 2 AND (SELECT
-- SQL: count(*) FROM pg_policies p WHERE p.schemaname = 'public' AND p.policyname IN
-- SQL: ('authenticity_audits_authenticated_all') AND p.qual LIKE '%current_org_id()%' AND
-- SQL: p.with_check LIKE '%current_org_id()%') = 1 AND (SELECT count(*) FROM pg_policies p WHERE
-- SQL: p.schemaname = 'public' AND p.policyname IN ('authenticity_audits_anon_deny') AND p.qual =
-- SQL: 'false') = 1
