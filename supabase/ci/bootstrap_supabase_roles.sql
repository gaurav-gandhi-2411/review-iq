-- Bootstrap the Supabase-platform behavior this app's own migrations assume already
-- exists (a vanilla Postgres container needs it created explicitly before this repo's
-- own migrations run; real Supabase projects get all of this from Supabase's own
-- platform bootstrap, never from anything in supabase/migrations/). Used by the
-- pre-cutover ephemeral-Postgres CI job (P3, 2026-08-01) -- see
-- .github/workflows/pre-cutover-verification.yml.

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'anon') THEN
    CREATE ROLE anon NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'authenticated') THEN
    CREATE ROLE authenticated NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'service_role') THEN
    CREATE ROLE service_role NOLOGIN BYPASSRLS;
  END IF;
END
$$;

-- Supabase pre-grants ALL privileges to `authenticated` on every table via ALTER
-- DEFAULT PRIVILEGES, at the platform level -- confirmed live 2026-08-01 by running
-- this repo's migrations against a vanilla postgres:17 container without this
-- statement: three RLS tests failed with a hard `InsufficientPrivilege` (permission
-- denied for table) instead of the expected "0 rows, RLS-denied" outcome, because
-- `authenticated` had no grant on the table AT ALL in vanilla Postgres, whereas in
-- real Supabase it does (RLS is the thing that then filters rows to zero -- the
-- grant itself was never the isolation boundary).
--
-- CORRECTED 2026-09-10 (Session 6 P5d, schema-drift-check.yml root cause -- carried
-- across two prior sessions without being fixed): the 2026-08-01 comment below (kept,
-- struck through in spirit, not deleted, per this repo's honesty-about-history
-- convention) claimed production shows ZERO table-level grants for `service_role`.
-- Re-verified live via a direct query against production's `pg_class.relacl` (not
-- `information_schema.role_table_grants`, which only shows grants visible to the
-- connecting role and returned zero rows misleadingly): `service_role` DOES now hold
-- full grants (`arwdDxtm`) on every table checked (`extraction_costs`, `extractions`,
-- `alert_log`). This is standard Supabase platform behavior -- `service_role` is
-- Supabase's own RLS-bypassing backend-admin primitive on every project, not something
-- unique to this repo's migrations -- and was either always true and mismeasured in
-- 2026-08-01, or became true via a later migration wave; either way, today's live
-- state is what the ephemeral comparison must match. This was the root cause of
-- ~130 of schema-drift-check.yml's ~134 nightly false-positive diffs.
--
-- Original 2026-08-01 comment, kept for history: "the original version of this file
-- also granted `anon` and `service_role` the same way, based on a since-corrected
-- memory note. A live schema diff against production (scripts/check_schema_drift.py)
-- proved that wrong -- production shows ZERO table-level grants for anon or
-- service_role on any table (verified via information_schema.role_table_grants
-- directly), only `authenticated`." -- `anon` remains correctly excluded below: a
-- fresh direct-ACL check found zero `anon` entries in any of the three tables' relacl,
-- consistent with the original finding for that role specifically.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO service_role;
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
