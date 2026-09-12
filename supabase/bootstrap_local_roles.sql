-- Bootstrap: standard Supabase-platform roles that every hosted Supabase project
-- provisions automatically, OUTSIDE the tracked migration workflow -- confirmed
-- 2026-08-16 (Item 141/142) by trying to apply this repo's migrations to a bare
-- postgres:16 container and hitting `role "authenticated" does not exist` on the
-- very first migration.
--
-- Grepped every migration file for role references (Item 141b) to confirm this is
-- the complete, minimal set this repo's migrations actually need -- not a generic
-- copy of Supabase's full local-dev role list, which also includes roles (e.g.
-- supabase_auth_admin, pgbouncer) this repo's own schema never references.
--
-- Run this ONCE, before applying any migration, against a throwaway/local Postgres
-- instance only. Never run against a real Supabase project -- these roles already
-- exist there; running CREATE ROLE again would simply error harmlessly, but there's
-- no reason to ever point this script at anything but a throwaway container.
--
-- Usage: psql "$LOCAL_PG_URL" -f supabase/bootstrap_local_roles.sql
--    or: python -c "..." (see the CI workflow that uses this file for the exact
--        invocation via supabase/push.py's own psycopg2 connection)

CREATE ROLE anon NOLOGIN NOINHERIT;
CREATE ROLE authenticated NOLOGIN NOINHERIT;
CREATE ROLE service_role NOLOGIN NOINHERIT BYPASSRLS;
