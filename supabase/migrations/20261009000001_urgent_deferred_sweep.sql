-- Migration: narrow SECURITY DEFINER cross-org listing for urgent-alert coalescing (S20 M3b-2).
--
-- Why: app/core/alerts/coalescer.py caps immediate high_urgency emails at 1 per org per 15
-- minutes and records the events past the cap as alert_log rows (event_type 'urgent_deferred').
-- Those rows are rolled into one summary email by the next window claim OR by the scheduled
-- digest sweep (POST /internal/digest/run). The sweep has no org context, and review_iq_app is
-- NOBYPASSRLS (ADR 0006), so it needs the same narrow resolver pattern as
-- list_orgs_with_daily_digest (20260817000003) to find orgs that still owe a roll-up.
--
-- EXPAND-ONLY: one new function; no table, column, grant or policy on an existing object is
-- changed. alert_log needs nothing new -- event_type has no CHECK constraint
-- (20260621000001_alerts.sql) and the coalescer only uses the existing SELECT/INSERT grants.
-- App code treats a missing function as "no orgs" (logs and continues), so deploying the code
-- before this migration is applied loses nothing: deferred rows simply wait.
--
-- Returns ONLY distinct org_id, never row content. Owned by review_iq_migrator, EXECUTE only
-- to review_iq_app.
-- Rollback: DROP FUNCTION public.list_orgs_with_deferred_urgent_alerts();

CREATE OR REPLACE FUNCTION public.list_orgs_with_deferred_urgent_alerts()
RETURNS TABLE(org_id uuid)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
STABLE
AS $$
  SELECT DISTINCT d.org_id
  FROM public.alert_log d
  WHERE d.event_type = 'urgent_deferred'
    AND NOT EXISTS (
      SELECT 1 FROM public.alert_log s
      WHERE s.org_id = d.org_id
        AND s.review_id = d.review_id
        AND s.event_type = 'high_urgency'
    );
$$;

COMMENT ON FUNCTION public.list_orgs_with_deferred_urgent_alerts IS
  'Cross-org sweep resolver for urgent-alert coalescing (app/core/alerts/storage.py::'
  'list_orgs_with_deferred_urgent_pg). Returns ONLY distinct org_id of orgs holding an '
  'undelivered urgent_deferred alert_log row. Owned by review_iq_migrator; same reasoning as '
  'list_orgs_with_daily_digest.';

ALTER FUNCTION public.list_orgs_with_deferred_urgent_alerts OWNER TO review_iq_migrator;
REVOKE ALL ON FUNCTION public.list_orgs_with_deferred_urgent_alerts FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.list_orgs_with_deferred_urgent_alerts TO review_iq_app;

-- ---------------------------------------------------------------------------
-- Postconditions (Session 15d) -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py. Each holds against the FINAL
-- schema state (a later migration must not undo it), in the CI build and in production.
-- ---------------------------------------------------------------------------
-- @postcondition: deferred_urgent_sweep_function_hardened
-- SQL: SELECT count(*) = 1 AND bool_and(p.prosecdef AND pg_get_userbyid(p.proowner) =
-- SQL: 'review_iq_migrator' AND p.proconfig @> ARRAY['search_path=public'] AND
-- SQL: has_function_privilege('review_iq_app', p.oid, 'EXECUTE') AND NOT
-- SQL: has_function_privilege('anon', p.oid, 'EXECUTE') AND NOT
-- SQL: has_function_privilege('authenticated', p.oid, 'EXECUTE') AND NOT
-- SQL: has_function_privilege('service_role', p.oid, 'EXECUTE')) FROM pg_proc p WHERE p.oid =
-- SQL: to_regprocedure('public.list_orgs_with_deferred_urgent_alerts()')
