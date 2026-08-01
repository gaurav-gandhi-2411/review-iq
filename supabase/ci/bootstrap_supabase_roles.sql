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

-- Supabase pre-grants ALL privileges to anon/authenticated/service_role on every table
-- via ALTER DEFAULT PRIVILEGES, at the platform level -- confirmed live 2026-08-01 by
-- running this repo's migrations against a vanilla postgres:17 container without this
-- statement: three RLS tests failed with a hard `InsufficientPrivilege` (permission
-- denied for table) instead of the expected "0 rows, RLS-denied" outcome, because
-- `anon`/`authenticated` had no grant on the table AT ALL in vanilla Postgres, whereas
-- in real Supabase they do (RLS is the thing that then filters rows to zero -- the
-- grant itself was never the isolation boundary). Matches this repo's own
-- feedback_supabase_grants memory. Must run BEFORE this repo's own migrations, so
-- every table they create inherits it exactly like production does.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO anon, authenticated, service_role;
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
