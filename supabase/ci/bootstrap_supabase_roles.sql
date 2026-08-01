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
-- CORRECTED 2026-08-01 (P0 schema-fidelity audit): the original version of this file
-- also granted `anon` and `service_role` the same way, based on a since-corrected
-- memory note. A live schema diff against production
-- (scripts/check_schema_drift.py) proved that wrong -- production shows ZERO
-- table-level grants for anon or service_role on any table (verified via
-- information_schema.role_table_grants directly), only `authenticated`. `anon`'s own
-- schema-level USAGE grant below matches what 20260510000001_create_tables.sql
-- itself already grants explicitly (`GRANT USAGE ON SCHEMA public TO authenticated,
-- anon`) -- anon's actual isolation comes entirely from the explicit "_anon_deny"
-- RLS policy every table carries (`USING (false)`), never from an absent table grant.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO authenticated;
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
