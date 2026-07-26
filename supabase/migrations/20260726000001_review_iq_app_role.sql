-- Migration: dedicated non-superuser role for the app's own runtime connection.
--
-- Security hardening, 2026-07-26. Before this, SUPABASE_DATABASE_URL connected as the
-- `postgres` superuser and relied on the app calling `SET LOCAL ROLE authenticated`
-- per-transaction (app/core/storage_pg.py) to self-restrict before touching tenant
-- tables. That worked, but the connection's BASE identity was still full superuser —
-- any code path that queried before that SET LOCAL call (or a raw psycopg2 usage that
-- forgot it) ran with unrestricted access to every schema in the cluster, not just this
-- app's own tables. Prompted by an audit of a sibling project (gg-portfolio) sharing
-- this same Supabase project for a different app (expense-tracker, isolated into its own
-- `expense` schema) — that audit flagged this app's own superuser connection as the one
-- remaining gap in mutual isolation between the two apps.
--
-- review_iq_app is LOGIN, NOT superuser, a member of `authenticated` (inherits every
-- existing GRANT + RLS policy `TO authenticated` automatically — see
-- 20260510000002_rls_policies.sql and every later migration's `GRANT ... TO
-- authenticated`, all already scoped to exactly this app's own tables in `public`), and
-- has BYPASSRLS set directly (matching Supabase's own `service_role`, which also has
-- BYPASSRLS and is also not superuser — see admin.py's org-creation/deletion paths, which
-- need to operate across the `org_id = current_org_id()` boundary the regular RLS
-- policies enforce and therefore cannot go through the same SET LOCAL ROLE authenticated
-- path). BYPASSRLS only affects row-level security filtering on tables the role already
-- has base grants on — it does NOT grant access to any schema/table the role wasn't
-- already granted (verified live: review_iq_app has zero privileges anywhere outside
-- `public`, including gg-portfolio's `expense` schema).
--
-- The actual password is generated and set out-of-band (never committed) — this
-- migration creates the role idempotently if it's missing, but a fresh checkout applying
-- this migration will get a role with no usable password until one is set via
-- `ALTER ROLE review_iq_app WITH PASSWORD '...'` (see ops/runbooks/secret-rotation.md).

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'review_iq_app') THEN
    CREATE ROLE review_iq_app LOGIN;
  END IF;
END
$$;

GRANT authenticated TO review_iq_app;
GRANT CONNECT ON DATABASE postgres TO review_iq_app;
ALTER ROLE review_iq_app BYPASSRLS;
