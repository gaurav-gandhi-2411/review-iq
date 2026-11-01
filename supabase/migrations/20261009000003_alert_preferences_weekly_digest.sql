-- Migration: accept frequency = 'weekly_digest' on alert_preferences, and give the weekly sweep
-- its own narrow cross-org resolver (S20 M3b, weekly digest).
--
-- Why two statements in one file: the app change (PUT /bff/alerts/preferences/{type} accepting
-- 'weekly_digest', POST /internal/digest/run?cadence=weekly) needs (1) the CHECK on
-- alert_preferences.frequency widened, otherwise the upsert fails with a constraint violation,
-- and (2) a way for the weekly sweep to list orgs across tenants. review_iq_app holds no
-- BYPASSRLS and no table grant on alert_preferences (20260817000003, 20260817000004), so the
-- listing goes through a SECURITY DEFINER function exactly like list_orgs_with_daily_digest.
--
-- Expand-only: the CHECK is WIDENED (every value that was legal stays legal), the function is
-- new. No row is rewritten and no existing reader changes: list_orgs_with_daily_digest still
-- matches only 'daily_digest', engine.py treats unknown-to-it values as it did before.
-- No watermark column is needed: the per-cadence watermark is alert_log.details->>'cadence',
-- a key inside the existing JSONB column.
--
-- Idempotent: the constraint is dropped IF EXISTS then re-added; the function is CREATE OR
-- REPLACE.
--
-- MIGRATION NOT APPLIED BY THE PR THAT ADDS THIS FILE: a human decides when it is applied.
-- Until it is applied, PUT frequency=weekly_digest returns a database error (500) and the weekly
-- sweep fails closed; daily behaviour is untouched.
-- Rollback (only after no row holds 'weekly_digest'; first run
--   UPDATE public.alert_preferences SET frequency = 'immediate' WHERE frequency = 'weekly_digest'):
--   ALTER TABLE public.alert_preferences DROP CONSTRAINT alert_preferences_frequency_check;
--   ALTER TABLE public.alert_preferences ADD CONSTRAINT alert_preferences_frequency_check
--     CHECK (frequency IN ('immediate', 'daily_digest'));
--   DROP FUNCTION public.list_orgs_with_weekly_digest();

ALTER TABLE public.alert_preferences DROP CONSTRAINT IF EXISTS alert_preferences_frequency_check;
ALTER TABLE public.alert_preferences ADD CONSTRAINT alert_preferences_frequency_check
  CHECK (frequency IN ('immediate', 'daily_digest', 'weekly_digest'));

CREATE OR REPLACE FUNCTION public.list_orgs_with_weekly_digest()
RETURNS TABLE(org_id uuid)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
STABLE
AS $$
  SELECT DISTINCT ap.org_id
  FROM public.alert_preferences ap
  WHERE ap.frequency = 'weekly_digest' AND ap.enabled = true;
$$;

COMMENT ON FUNCTION public.list_orgs_with_weekly_digest IS
  'S20 M3b: weekly counterpart of list_orgs_with_daily_digest. Narrow SECURITY DEFINER '
  'cross-org sweep used by POST /internal/digest/run?cadence=weekly '
  '(app/core/alerts/storage.py::list_orgs_with_weekly_digest_pg). Returns ONLY distinct '
  'org_id -- never a preference row''s content. Owned by review_iq_migrator.';

ALTER FUNCTION public.list_orgs_with_weekly_digest OWNER TO review_iq_migrator;
REVOKE ALL ON FUNCTION public.list_orgs_with_weekly_digest FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.list_orgs_with_weekly_digest TO review_iq_app;

-- ---------------------------------------------------------------------------
-- Postconditions -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py.
-- ---------------------------------------------------------------------------
-- @postcondition: alert_preferences_frequency_allows_weekly_digest
-- SQL: SELECT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conrelid =
-- SQL: 'public.alert_preferences'::regclass AND c.contype = 'c' AND c.conname =
-- SQL: 'alert_preferences_frequency_check' AND pg_get_constraintdef(c.oid) LIKE '%immediate%' AND
-- SQL: pg_get_constraintdef(c.oid) LIKE '%daily_digest%' AND pg_get_constraintdef(c.oid) LIKE
-- SQL: '%weekly_digest%')

-- @postcondition: weekly_digest_resolver_hardened
-- SQL: SELECT count(*) = 1 AND bool_and(p.prosecdef AND pg_get_userbyid(p.proowner) =
-- SQL: 'review_iq_migrator' AND p.proconfig @> ARRAY['search_path=public'] AND
-- SQL: has_function_privilege('review_iq_app', p.oid, 'EXECUTE') AND NOT
-- SQL: has_function_privilege('anon', p.oid, 'EXECUTE') AND NOT
-- SQL: has_function_privilege('authenticated', p.oid, 'EXECUTE') AND NOT
-- SQL: has_function_privilege('service_role', p.oid, 'EXECUTE')) FROM pg_proc p WHERE p.oid =
-- SQL: to_regprocedure('public.list_orgs_with_weekly_digest()')
