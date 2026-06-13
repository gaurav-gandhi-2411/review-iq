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
