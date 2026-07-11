-- Migration: Row-Level Security for quota_requests table.
--
-- public.quota_requests already exists in the live database (created outside the
-- migration workflow at some earlier point -- no prior migration file for it in this
-- repo) and was left with Supabase's default PostgREST grants: anon and authenticated
-- both hold INSERT/SELECT/UPDATE/DELETE/TRUNCATE, and RLS was never enabled. Found
-- 2026-07-11 while investigating audit finding #3.
--
-- Live impact confirmed before this migration: ANY caller holding only the public
-- Supabase anon key (ships in the frontend bundle, not a secret) can INSERT, SELECT,
-- UPDATE, DELETE, or TRUNCATE this table directly via Supabase's auto-generated REST
-- API (PostgREST) -- entirely bypassing the FastAPI app. Table is currently empty (0
-- rows verified 2026-07-11), so no data has actually been read/tampered with, but the
-- door has been open since whenever this table was created.
--
-- Fix, matching the established pattern (see 20260613000001_batch_jobs_rls.sql,
-- 20260619000003_corrections.sql): enable RLS with an org_id = current_org_id() policy
-- for authenticated, anon denied outright. RLS does NOT govern TRUNCATE (a Postgres
-- privilege, not a row-level check) -- explicit REVOKE below closes that separately.
--
-- No app code currently reads this table (no list/read endpoint exists -- see audit),
-- and record_quota_request_pg() writes via the pooler's base connection role, not
-- `authenticated` (no _set_tenant() call there, paired app-code fix tracked
-- separately). authenticated gets INSERT + SELECT only (mirrors a future read
-- endpoint being addable without another migration); no UPDATE/DELETE -- this is a
-- write-once interest-signal log, matching alert_log's append-only pattern.

-- ---------------------------------------------------------------------------
-- Revoke the dangerous default grants first
-- ---------------------------------------------------------------------------
REVOKE ALL ON public.quota_requests FROM anon;
REVOKE ALL ON public.quota_requests FROM authenticated;
REVOKE ALL ON public.quota_requests FROM PUBLIC;

GRANT SELECT, INSERT ON public.quota_requests TO authenticated;
-- anon gets nothing — all access must be via API key + service_role, same as every
-- other tenant table in this schema.

-- ---------------------------------------------------------------------------
-- Row-Level Security
-- ---------------------------------------------------------------------------
ALTER TABLE public.quota_requests ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  DROP POLICY IF EXISTS "quota_requests_authenticated_all" ON public.quota_requests;
  DROP POLICY IF EXISTS "quota_requests_anon_deny"         ON public.quota_requests;
END $$;

CREATE POLICY "quota_requests_authenticated_all" ON public.quota_requests
  FOR ALL TO authenticated
  USING     (org_id = public.current_org_id())
  WITH CHECK (org_id = public.current_org_id());

CREATE POLICY "quota_requests_anon_deny" ON public.quota_requests
  FOR ALL TO anon USING (false);
