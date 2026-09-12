-- Migration: narrow authenticated's grants on extraction_costs to INSERT, SELECT only.
--
-- Found 2026-08-17 (Item 197): 20260710235958_capture_extraction_costs.sql faithfully
-- reproduced this table's live grants as found -- authenticated held DELETE, INSERT,
-- REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE, seven privileges. RLS is correctly
-- enabled with an org_id = current_org_id() policy (extraction_costs_authenticated_all)
-- and anon is denied (extraction_costs_anon_deny) -- neither of those is touched by
-- this migration. But RLS does NOT govern TRUNCATE (a Postgres table-level privilege,
-- not a row-level check) -- exactly the same gap 20260711000002_quota_requests_rls.sql's
-- own comment documents and closes for its sibling table ("RLS does NOT govern
-- TRUNCATE... explicit REVOKE below closes that separately"). That fix pattern was
-- never applied here: any `authenticated` connection, any org, could TRUNCATE this
-- entire table -- wiping every org's cost data -- and RLS cannot stop it.
--
-- Currently latent, not active: nothing in app/ writes to or reads from this table
-- today -- the feature that would (app/core/pricing.py, cost telemetry) lives entirely
-- on unmerged PR #24. Confirmed via that PR's own diff: it performs exactly two
-- operations -- a tenant-scoped INSERT (per-extraction cost recording, through
-- _set_tenant()) and a cross-org SELECT (admin COGS aggregation, through
-- review_iq_admin -- a separate role holding its own BYPASSRLS, never `authenticated` --
-- unaffected by this narrowing). Fixed now, before #24 merges and this table goes live.

REVOKE ALL ON public.extraction_costs FROM anon;
REVOKE ALL ON public.extraction_costs FROM authenticated;
REVOKE ALL ON public.extraction_costs FROM PUBLIC;

GRANT SELECT, INSERT ON public.extraction_costs TO authenticated;
-- anon gets nothing -- matches the existing extraction_costs_anon_deny RLS policy and
-- every other tenant table in this schema (mirrors quota_requests exactly).
