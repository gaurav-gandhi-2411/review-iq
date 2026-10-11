-- ci_readonly: the ONLY database credential CI may hold (GG decision S23: no write-capable
-- production credential in CI). NOT a migration -- applied by hand, locally, as a role that can
-- CREATE ROLE and is a member of review_iq_migrator (the Supabase `postgres` role), via:
--
--   CI_READONLY_PASSWORD=<from Secret Manager> psql "$ADMIN_DSN" -f supabase/ops/ci_readonly_role.sql
--
-- Password comes from the environment (\getenv), never argv or this file. Idempotent: a rerun
-- re-asserts every attribute and grant and rotates the password.
--
-- BYPASSRLS is deliberate and unavoidable: every tenant table has RLS enabled with policies only
-- TO anon/authenticated, so a non-owner role without BYPASSRLS either makes pg_dump fail
-- (row_security=off) or silently dump zero rows (--enable-row-security). The only other way to
-- read every row is table ownership, which is write-capable. With SELECT-only grants BYPASSRLS
-- is read-only in effect, but it can read every tenant's rows: a named exception to the S0 rule
-- that review_iq_app never holds BYPASSRLS (see docs/ops/ci-readonly-role.md).
\set ON_ERROR_STOP on
\getenv ro_password CI_READONLY_PASSWORD
-- Fails closed: an unset variable is a syntax error here, an empty/short one is rejected.
SELECT (length(:'ro_password') >= 24) AS pw_ok \gset
\if :pw_ok
\else
  \echo 'ERROR: CI_READONLY_PASSWORD must be set and >= 24 chars'
  \quit 1
\endif

SELECT 'CREATE ROLE ci_readonly LOGIN' WHERE NOT EXISTS
  (SELECT 1 FROM pg_roles WHERE rolname = 'ci_readonly') \gexec
SELECT format('ALTER ROLE ci_readonly WITH LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE '
              'NOREPLICATION BYPASSRLS CONNECTION LIMIT 5 PASSWORD %L', :'ro_password') \gexec
-- Soft guard only (a session may SET it back); the real wall is the absence of write grants.
ALTER ROLE ci_readonly SET default_transaction_read_only = on;

REVOKE ALL ON DATABASE :"DBNAME" FROM ci_readonly;
GRANT CONNECT ON DATABASE :"DBNAME" TO ci_readonly;
REVOKE ALL ON SCHEMA public FROM ci_readonly;
GRANT USAGE ON SCHEMA public TO ci_readonly;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM ci_readonly;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM ci_readonly;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM ci_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ci_readonly;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO ci_readonly;

-- Future tables: default privileges are per CREATING role, so cover the invoker and the
-- migrator (which creates tables via push.py) when we are a member of it.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO ci_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON SEQUENCES TO ci_readonly;
SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT ON %s TO ci_readonly',
              r.rolname, k.kind)
FROM pg_roles r CROSS JOIN (VALUES ('TABLES'), ('SEQUENCES')) k(kind)
WHERE r.rolname = 'review_iq_migrator' AND r.rolname <> current_user
  AND pg_has_role(current_user, r.oid, 'USAGE') \gexec

-- Fail the script if the result is not what the runbook promises.
DO $$
BEGIN
  IF has_schema_privilege('ci_readonly', 'public', 'CREATE') THEN
    RAISE EXCEPTION 'ci_readonly can CREATE in public (PUBLIC grant?) -- fix before use';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
               AND NOT has_table_privilege('ci_readonly', c.oid, 'SELECT')) THEN
    RAISE EXCEPTION 'ci_readonly cannot SELECT every public table';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'S')
               AND (has_table_privilege('ci_readonly', c.oid, 'INSERT')
                 OR has_table_privilege('ci_readonly', c.oid, 'UPDATE')
                 OR has_table_privilege('ci_readonly', c.oid, 'DELETE')
                 OR has_table_privilege('ci_readonly', c.oid, 'TRUNCATE'))) THEN
    RAISE EXCEPTION 'ci_readonly holds a write privilege on a public relation';
  END IF;
END $$;
