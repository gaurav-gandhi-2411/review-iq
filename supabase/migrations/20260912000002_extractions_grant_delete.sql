-- Migration: grant DELETE on public.extractions to authenticated (Session 12 P2c).
--
-- Real bug found by RUNNING the integration test against production, not by review:
-- 20260817000004_wave2_grant_narrowing.sql deliberately scoped extractions to
-- SELECT, INSERT only (no UPDATE/DELETE was needed at the time -- account deletion goes
-- through ON DELETE CASCADE from organizations, which does not require the deleting
-- role to hold DELETE on the child table). This session's purge feature
-- (purge_org_extractions_pg, app/core/storage_pg.py) is the first code path that ever
-- needs to DELETE FROM extractions directly, and it failed live:
--   psycopg2.errors.InsufficientPrivilege: permission denied for table extractions
--   HINT: Grant the required privileges to the current role with:
--   GRANT DELETE ON public.extractions TO authenticated;
-- (Postgres's own error message is the exact fix -- verified against the live grants
-- and the existing extractions_authenticated_all RLS policy, which is already
-- FOR ALL / USING (org_id = current_org_id()) and therefore already correctly scopes
-- DELETE to the calling org's own rows once the command-level grant permits it at all.)

GRANT DELETE ON public.extractions TO authenticated;
