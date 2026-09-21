-- Migration: Row-Level Security for batch_jobs table.
-- Pattern matches existing tenant tables: same current_org_id() helper, same
-- USING + WITH CHECK on authenticated, anon deny.
-- Date: 2026-06-13
--
-- NOTE: public.current_org_id() is already defined in 20260510000002_rls_policies.sql.
-- This migration does NOT redefine it.

-- ---------------------------------------------------------------------------
-- Row-Level Security
-- ---------------------------------------------------------------------------
ALTER TABLE public.batch_jobs ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  DROP POLICY IF EXISTS "batch_jobs_authenticated_all" ON public.batch_jobs;
  DROP POLICY IF EXISTS "batch_jobs_anon_deny"         ON public.batch_jobs;
END $$;

CREATE POLICY "batch_jobs_authenticated_all" ON public.batch_jobs
  FOR ALL TO authenticated
  USING     (org_id = public.current_org_id())
  WITH CHECK (org_id = public.current_org_id());

CREATE POLICY "batch_jobs_anon_deny" ON public.batch_jobs
  FOR ALL TO anon USING (false);

-- ---------------------------------------------------------------------------
-- Postconditions (Session 15d) -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py. Each holds against the FINAL
-- schema state (a later migration must not undo it), in the CI build and in production.
-- ---------------------------------------------------------------------------
-- @postcondition: batch_jobs_rls_and_policies
-- SQL: SELECT (SELECT count(*) = 1 AND bool_and(c.relrowsecurity) FROM pg_class c WHERE c.oid IN
-- SQL: ('public.batch_jobs'::regclass)) AND (SELECT count(*) FROM pg_policies p WHERE p.schemaname
-- SQL: = 'public' AND (p.tablename, p.policyname, p.cmd, p.roles::text) IN (('batch_jobs',
-- SQL: 'batch_jobs_authenticated_all', 'ALL', '{authenticated}'), ('batch_jobs',
-- SQL: 'batch_jobs_anon_deny', 'ALL', '{anon}'))) = 2 AND (SELECT count(*) FROM pg_policies p WHERE
-- SQL: p.schemaname = 'public' AND p.policyname IN ('batch_jobs_authenticated_all') AND p.qual LIKE
-- SQL: '%current_org_id()%' AND p.with_check LIKE '%current_org_id()%') = 1 AND (SELECT count(*)
-- SQL: FROM pg_policies p WHERE p.schemaname = 'public' AND p.policyname IN
-- SQL: ('batch_jobs_anon_deny') AND p.qual = 'false') = 1
