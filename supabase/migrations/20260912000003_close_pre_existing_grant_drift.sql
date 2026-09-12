-- Migration: close the two pre-existing schema drifts identified before this session
-- (schema-drift-check.yml has been flagging these since at least 2026-08-14, unrelated
-- to Session 13's own retention-mode migrations).
--
-- Finding 1 -- anon has EXECUTE on public.current_org_id(): present in production,
-- absent from the migration-built schema. Root-caused: current_org_id() is a
-- SECURITY DEFINER function (20260510000002_rls_policies.sql) with no explicit REVOKE,
-- so it kept Postgres's default PUBLIC-inherited EXECUTE grant, which anon inherits.
-- Severity: LOW (the function is table-free, STABLE, and only reads session-local
-- request.jwt.claims / app.current_org_id -- calling it directly returns at most the
-- caller's own already-known org context, never another tenant's data), but anon has no
-- legitimate reason to call it, so this narrows to least privilege (rule 96) rather than
-- leaving an unexplained grant in place.
REVOKE EXECUTE ON FUNCTION public.current_org_id() FROM PUBLIC, anon;

-- Finding 2 -- service_role is missing its standard default-privilege grants on
-- public.demo_daily_usage: present in the migration-built (CI ephemeral) schema, absent
-- from production. Root-caused: supabase/ci/bootstrap_supabase_roles.sql's
-- `ALTER DEFAULT PRIVILEGES ... GRANT ALL ON TABLES TO service_role` (standard Supabase
-- platform behavior, per that file's own comment) means every FRESH table in the CI
-- ephemeral database automatically gets this grant -- production's demo_daily_usage
-- (created 20260905000001) apparently did not inherit it, for reasons not fully traced
-- (possibly how the migration was originally applied). service_role is a fully-trusted,
-- BYPASSRLS-equivalent internal Supabase role (dashboard/service tooling), not a
-- customer-facing security boundary, so this drift's severity is LOW regardless of
-- direction -- this migration simply brings production back in line with standard
-- platform convention.
GRANT ALL ON public.demo_daily_usage TO service_role;
