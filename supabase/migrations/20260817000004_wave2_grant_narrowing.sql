-- Migration: narrow grants on Wave 2's six tables from Item 204's audit (organizations,
-- api_keys, extractions, usage_records, alert_log, authenticity_audits). Wave 1
-- (20260817000002) covered the four zero/near-zero tables; the three tables needing extra
-- due diligence (batch_jobs, alert_preferences, corrections, Item 207) are deliberately
-- deferred to Wave 3 -- staged one wave at a time, verified in production before the next,
-- per GG's instruction.
--
-- All six carry Supabase's default PostgREST auto-grant to authenticated and anon (DELETE,
-- INSERT, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE) -- far wider than app/ uses. RLS is
-- already enabled on all six (20260510000002_rls_policies.sql,
-- 20260611000001_authenticity_audits.sql, 20260621000001_alerts.sql) -- this migration is
-- grant narrowing only, same RLS-does-not-govern-TRUNCATE gap as every prior wave.
--
-- organizations: authenticated needs SELECT (app/api/account.py:185) and DELETE
-- (app/api/account.py:230, _do_delete_org -- a real, tested account-deletion feature, not an
-- over-grant to narrow away). No INSERT -- create_org_and_membership (SECURITY DEFINER,
-- 20260801000002) handles org creation. No UPDATE found anywhere as authenticated.
--
-- api_keys: authenticated needs SELECT, INSERT, UPDATE. Two DIRECT (non-SECURITY-DEFINER)
-- INSERT sites confirmed by re-reading app/ for this migration (Item 235):
-- app/api/bff/router.py:437 (self-serve API key creation, under _set_tenant()) and
-- app/api/account.py:148 (/regenerate-key, also under _set_tenant()) -- both bypass
-- create_api_key_for_org entirely. UPDATE needed for revoked_at (account.py:143,
-- bff/router.py:500) and last_used_at (auth/api_key.py:128, auth/session.py:106).
--
-- review_iq_admin (app/api/admin.py, connects via ADMIN_DATABASE_URL, keeps its own
-- BYPASSRLS post-cutover -- confirmed unaffected by this migration) does direct INSERT on
-- BOTH organizations and api_keys (admin.py:95,152,244) and UPDATE on api_keys
-- (admin.py:230,277). Until now this worked only by INHERITING authenticated's grant via
-- role membership (GRANT authenticated TO review_iq_admin, 20260726000001) -- a fragile
-- dependency: narrowing authenticated's INSERT away, as this migration otherwise would for
-- organizations, would have silently broken org/key creation via the admin service with no
-- code change and no error until the next admin request. Fixed here by giving
-- review_iq_admin its own EXPLICIT direct grants, independent of whatever authenticated
-- holds -- matching this schema's established pattern of not relying on inherited privilege
-- for functionality that must survive independently (the same lesson SECURITY DEFINER
-- ownership already encodes for the resolver functions).
--
-- extractions: SELECT, INSERT (app/core/storage_pg.py -- create_extraction_pg,
-- list_dated_extractions_pg, list_extractions_pg, aggregate_extractions_pg). No UPDATE/DELETE
-- as authenticated anywhere -- extractions are write-once from the app's perspective.
--
-- usage_records: SELECT, INSERT, UPDATE. INSERT + quota-count SELECT
-- (app/auth/api_key.py, app/auth/session.py, app/api/account.py, app/api/bff/router.py),
-- UPDATE for tokens_in/tokens_out post-LLM-call (app/core/storage_pg.py:206).
--
-- alert_log: SELECT, INSERT. Dedupe-check SELECT and digest-lookback SELECT MAX, plus INSERT
-- for new alert rows (app/core/alerts/storage.py). No UPDATE/DELETE -- append-only, same
-- pattern as quota_requests.
--
-- authenticity_audits: SELECT, INSERT. INSERT audit result, SELECT COUNT/AVG for aggregation
-- (app/core/storage_pg.py). No UPDATE/DELETE as authenticated anywhere.

REVOKE ALL ON public.organizations       FROM anon;
REVOKE ALL ON public.organizations       FROM authenticated;
REVOKE ALL ON public.organizations       FROM PUBLIC;

REVOKE ALL ON public.api_keys            FROM anon;
REVOKE ALL ON public.api_keys            FROM authenticated;
REVOKE ALL ON public.api_keys            FROM PUBLIC;

REVOKE ALL ON public.extractions         FROM anon;
REVOKE ALL ON public.extractions         FROM authenticated;
REVOKE ALL ON public.extractions         FROM PUBLIC;

REVOKE ALL ON public.usage_records       FROM anon;
REVOKE ALL ON public.usage_records       FROM authenticated;
REVOKE ALL ON public.usage_records       FROM PUBLIC;

REVOKE ALL ON public.alert_log           FROM anon;
REVOKE ALL ON public.alert_log           FROM authenticated;
REVOKE ALL ON public.alert_log           FROM PUBLIC;

REVOKE ALL ON public.authenticity_audits FROM anon;
REVOKE ALL ON public.authenticity_audits FROM authenticated;
REVOKE ALL ON public.authenticity_audits FROM PUBLIC;

GRANT SELECT, DELETE          ON public.organizations       TO authenticated;
GRANT SELECT, INSERT, UPDATE  ON public.api_keys            TO authenticated;
GRANT SELECT, INSERT          ON public.extractions         TO authenticated;
GRANT SELECT, INSERT, UPDATE  ON public.usage_records       TO authenticated;
GRANT SELECT, INSERT          ON public.alert_log           TO authenticated;
GRANT SELECT, INSERT          ON public.authenticity_audits TO authenticated;

-- review_iq_admin: explicit, independent grants -- do not rely on its inherited membership
-- in authenticated for these. Only organizations and api_keys are touched by app/api/admin.py
-- (confirmed by grep -- no other Wave 2 table appears in that file).
GRANT SELECT, INSERT              ON public.organizations TO review_iq_admin;
GRANT SELECT, INSERT, UPDATE      ON public.api_keys      TO review_iq_admin;

-- anon gets nothing on any of the six -- matches every other tenant table in this schema.
