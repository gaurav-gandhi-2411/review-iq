-- Migration: narrow authenticated's grants on the four lowest-risk tables from Item 204's
-- audit (Wave 1 of the TRUNCATE-grant remediation -- see 20260817000001 for extraction_costs,
-- the first table fixed under this effort).
--
-- All four tables carry Supabase's default PostgREST auto-grant to authenticated (DELETE,
-- INSERT, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE -- 7 privileges), which is far wider
-- than what app/ actually uses. RLS is correctly enabled on all four already (unchanged by
-- this migration) -- this is grant narrowing only, closing the same RLS-does-not-govern-
-- TRUNCATE gap as 20260711000002_quota_requests_rls.sql and 20260817000001.
--
-- organization_members: no direct app-code access as `authenticated` at all. Every read/write
-- goes through the SECURITY DEFINER functions added in 20260801000002
-- (resolve_org_for_user, create_org_and_membership), which run as review_iq_migrator, not
-- authenticated. Confirmed via exhaustive grep of app/ (Item 207/208, 2026-08-17):
-- authenticated needs zero privileges on this table.
--
-- google_business_installations / shopify_installations: app/ only ever SELECTs these
-- (webhook lookup, both tenant-scoped via _set_tenant()). The INSERT/UPDATE (upsert) path for
-- both was already redirected to SECURITY DEFINER functions in 20260801000002
-- (upsert_google_installation, upsert_shopify_installation) -- their own migration comments
-- state "authenticated only holds a SELECT policy... there has never been an INSERT/UPDATE
-- policy for authenticated on either table". authenticated needs SELECT only.
--
-- batch_job_rows: the cross-org claim/settle path (claim_pending_batch_job_row,
-- settle_batch_job_row, both SECURITY DEFINER) never runs as authenticated. But
-- app/core/storage_pg.py's enqueue_batch_job_rows_pg/count_pending_rows_pg/
-- count_job_row_statuses_pg/list_job_row_hashes_pg DO run as authenticated via
-- _set_tenant() -- INSERT at submit time, SELECT for count/status/hash lookups. No UPDATE
-- as authenticated anywhere (settle's UPDATE is fully inside the SECURITY DEFINER function).
-- authenticated needs SELECT, INSERT.

REVOKE ALL ON public.organization_members         FROM anon;
REVOKE ALL ON public.organization_members         FROM authenticated;
REVOKE ALL ON public.organization_members         FROM PUBLIC;

REVOKE ALL ON public.google_business_installations FROM anon;
REVOKE ALL ON public.google_business_installations FROM authenticated;
REVOKE ALL ON public.google_business_installations FROM PUBLIC;

REVOKE ALL ON public.shopify_installations         FROM anon;
REVOKE ALL ON public.shopify_installations         FROM authenticated;
REVOKE ALL ON public.shopify_installations         FROM PUBLIC;

REVOKE ALL ON public.batch_job_rows                FROM anon;
REVOKE ALL ON public.batch_job_rows                FROM authenticated;
REVOKE ALL ON public.batch_job_rows                FROM PUBLIC;

-- organization_members: no GRANT at all -- authenticated needs nothing.

GRANT SELECT ON public.google_business_installations TO authenticated;
GRANT SELECT ON public.shopify_installations         TO authenticated;

GRANT SELECT, INSERT ON public.batch_job_rows        TO authenticated;

-- anon gets nothing on any of the four -- matches every other tenant table in this schema.
