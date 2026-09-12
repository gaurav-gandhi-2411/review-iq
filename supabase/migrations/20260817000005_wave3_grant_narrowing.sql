-- Migration: narrow grants on Wave 3's three tables from Item 204's audit (batch_jobs,
-- alert_preferences, corrections) -- the tables Item 207 flagged as needing extra due
-- diligence before Wave 1/2 (ON CONFLICT DO UPDATE requiring UPDATE privilege even on the
-- no-op arm, and a real read path for corrections that wasn't obvious from a first pass).
-- Final wave -- Waves 1 (20260817000002) and 2 (20260817000004) already covered the other
-- ten tables from the original 13-table audit plus extraction_costs.
--
-- All three carry Supabase's default PostgREST auto-grant (7 privileges) to authenticated
-- and anon. RLS is already enabled on all three (20260613000001_batch_jobs_rls.sql,
-- 20260619000003_corrections.sql, 20260621000001_alerts.sql) -- grant narrowing only.
--
-- batch_jobs: SELECT, INSERT, UPDATE. No DELETE anywhere in app/ -- org deletion relies on
-- ON DELETE CASCADE from organizations (batch_jobs.org_id REFERENCES organizations(id) ON
-- DELETE CASCADE, 20260511000006_batch_jobs.sql), not a direct authenticated DELETE.
-- create_batch_job_pg (INSERT), get_batch_job_pg (SELECT), update_batch_job_pg (UPDATE) --
-- all app/core/storage_pg.py, all _set_tenant()-scoped.
--
-- alert_preferences: SELECT, INSERT, UPDATE. app/core/alerts/storage.py's
-- upsert_preference_pg does INSERT ... ON CONFLICT (org_id, event_type) DO UPDATE -- the DO
-- UPDATE arm requires UPDATE privilege on the table regardless of whether a conflict
-- actually occurs on any given call. SELECT reads confirmed at storage.py:95,118,399.
--
-- corrections: SELECT, INSERT. Real read path: app/core/dataset/builder.py, tenant-scoped
-- via _set_tenant(), feeding the /v2/dataset endpoint (SELECT ... FROM public.corrections
-- WHERE org_id = %s). Write path: app/core/corrections/service.py, also _set_tenant()-scoped
-- (INSERT INTO public.corrections). No UPDATE/DELETE call site anywhere in app/.
--
-- No admin.py involvement in any of the three (confirmed via grep, same check as Waves 1/2)
-- -- no review_iq_admin grants needed here.

REVOKE ALL ON public.batch_jobs         FROM anon;
REVOKE ALL ON public.batch_jobs         FROM authenticated;
REVOKE ALL ON public.batch_jobs         FROM PUBLIC;

REVOKE ALL ON public.alert_preferences  FROM anon;
REVOKE ALL ON public.alert_preferences  FROM authenticated;
REVOKE ALL ON public.alert_preferences  FROM PUBLIC;

REVOKE ALL ON public.corrections        FROM anon;
REVOKE ALL ON public.corrections        FROM authenticated;
REVOKE ALL ON public.corrections        FROM PUBLIC;

GRANT SELECT, INSERT, UPDATE ON public.batch_jobs        TO authenticated;
GRANT SELECT, INSERT, UPDATE ON public.alert_preferences TO authenticated;
GRANT SELECT, INSERT         ON public.corrections       TO authenticated;

-- anon gets nothing on any of the three -- matches every other tenant table in this schema.
