-- Migration: narrow SECURITY DEFINER replacements for the two cross-org scheduled-sweep
-- queries that currently depend on review_iq_app holding BYPASSRLS.
--
-- Found 2026-08-17 while preparing to run the S0 cutover (ALTER ROLE review_iq_app
-- NOBYPASSRLS): app/core/storage_pg.py's list_orgs_with_dated_extractions_pg and
-- app/core/alerts/storage.py's list_orgs_with_daily_digest_pg both connect via
-- _db_connect() (review_iq_app) and deliberately never call _set_tenant() -- their own
-- docstrings call this an intentional "service-role bypass pattern" for a scheduled sweep
-- that must see every org. review_iq_app is a member of `authenticated` (GRANT authenticated
-- TO review_iq_app, 20260726000001), so it is already subject to authenticated's RLS
-- policies by membership alone, with no explicit SET ROLE needed -- the ONLY reason these
-- two queries currently see cross-org rows is BYPASSRLS. Confirmed empirically, not just
-- reasoned: applied every migration + the cutover statement to a throwaway container,
-- inserted extraction rows for two distinct orgs, then ran the exact query from
-- list_orgs_with_dated_extractions_pg as review_iq_app with no SET ROLE and no org context
-- -- 0 rows returned despite 2 rows existing. Once the cutover runs, both functions go
-- silently dark (empty result, not an error) -- app/core/alerts/detector_sweep.py's proactive
-- alert-detection sweep and app/api/internal/digest.py's daily-digest job both stop finding
-- any org to process, org-wide, with nothing to signal the failure.
--
-- A full audit of every _db_connect()/SUPABASE_DATABASE_URL call site in app/ (2026-08-17)
-- found no other instance of this pattern -- every other cross-org need already goes
-- through a SECURITY DEFINER resolver added in 20260801000001/20260801000002, or is
-- app/api/ops.py's health-check SELECT 1 (touches no table, RLS-irrelevant).
--
-- Fix: same narrow SECURITY DEFINER pattern as the webhook/auth resolvers -- owned by
-- review_iq_migrator, EXECUTE granted only to review_iq_app, never to authenticated. Each
-- returns ONLY the distinct org_id list the sweep needs, nothing else from the underlying
-- rows.

CREATE OR REPLACE FUNCTION public.list_orgs_with_dated_extractions()
RETURNS TABLE(org_id uuid)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
STABLE
AS $$
  SELECT DISTINCT e.org_id
  FROM public.extractions e
  WHERE e.review_date IS NOT NULL;
$$;

COMMENT ON FUNCTION public.list_orgs_with_dated_extractions IS
  'BYPASSRLS remediation: narrow SECURITY DEFINER replacement for the cross-org sweep '
  'previously done via review_iq_app''s BYPASSRLS (app/core/alerts/detector_sweep.py, via '
  'app/core/storage_pg.py::list_orgs_with_dated_extractions_pg). Returns ONLY distinct '
  'org_id -- never a row''s content. Owned by review_iq_migrator (schema-scoped, not '
  'database-wide); the calling role (review_iq_app) needs no direct SELECT on extractions '
  'for this specific cross-org listing.';

ALTER FUNCTION public.list_orgs_with_dated_extractions OWNER TO review_iq_migrator;
REVOKE ALL ON FUNCTION public.list_orgs_with_dated_extractions FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.list_orgs_with_dated_extractions TO review_iq_app;

CREATE OR REPLACE FUNCTION public.list_orgs_with_daily_digest()
RETURNS TABLE(org_id uuid)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
STABLE
AS $$
  SELECT DISTINCT ap.org_id
  FROM public.alert_preferences ap
  WHERE ap.frequency = 'daily_digest' AND ap.enabled = true;
$$;

COMMENT ON FUNCTION public.list_orgs_with_daily_digest IS
  'BYPASSRLS remediation: narrow SECURITY DEFINER replacement for the cross-org sweep '
  'previously done via review_iq_app''s BYPASSRLS (app/api/internal/digest.py, via '
  'app/core/alerts/storage.py::list_orgs_with_daily_digest_pg). Returns ONLY distinct '
  'org_id -- never a preference row''s content. Owned by review_iq_migrator, same '
  'reasoning as list_orgs_with_dated_extractions above.';

ALTER FUNCTION public.list_orgs_with_daily_digest OWNER TO review_iq_migrator;
REVOKE ALL ON FUNCTION public.list_orgs_with_daily_digest FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.list_orgs_with_daily_digest TO review_iq_app;
