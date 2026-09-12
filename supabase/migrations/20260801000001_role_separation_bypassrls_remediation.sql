-- Migration: role separation to close the BYPASSRLS reachability finding (S0). NOT applied
-- by this session -- see ops/runbooks/bypassrls-remediation-cutover.md for the exact
-- sequence this must be run in; order matters (see statement 4's own comment).
--
-- Recovered and adapted from the original attempt at this fix (PR #33,
-- fix/wave1-s0-bypassrls-remediation), which was merged into a stacked branch that never
-- actually reached `main` -- see PLAN.md's phantom-merge audit entry. The role-separation
-- design below is that PR's, re-verified against the live database rather than assumed;
-- two additions beyond the original scope are folded in (statements 5 and 6).
--
-- Prior state (20260726000001_review_iq_app_role.sql, confirmed live 2026-08-01 by direct
-- query, not assumed): review_iq_app holds BYPASSRLS and is the ONLY role every
-- request-serving code path in app/ authenticates as (including the "admin" Cloud Run
-- service -- ADMIN_DATABASE_URL currently points at the exact same Secret Manager secret as
-- SUPABASE_DATABASE_URL, confirmed via `gcloud run services describe`; review_iq_admin does
-- not exist as a role at all yet). RLS provided zero protection to any of the 13 code paths
-- that connect via this role without calling _set_tenant() first -- correctness rested
-- entirely on every current and future function remembering to do so, with no
-- database-level backstop if one didn't.
--
-- New topology -- three roles, each with the minimum privilege its one job needs:
--
--   review_iq_migrator  -- BYPASSRLS, broad DML/DDL on the `public` schema ONLY (not
--                          superuser -- no access to this shared Supabase project's other
--                          schemas, e.g. the sibling `expense` app's schema). LOGIN, but its
--                          password is generated and set out-of-band, and it is NEVER
--                          referenced by any application setting, Cloud Run env var, or
--                          Secret Manager secret used by the deployed app -- reachability
--                          from a request-serving path must be proven by grepping every
--                          DSN/connection-string construction in app/ and scripts/, not
--                          assumed.
--
--   review_iq_app        -- BYPASSRLS REMOVED (statement 4, the core fix). Continues to be
--                          the only role SUPABASE_DATABASE_URL authenticates as. Every
--                          tenant-scoped query must already call _set_tenant() before
--                          touching tenant data, OR go through one of the narrow
--                          SECURITY DEFINER lookups added below (statement 3) for the two
--                          webhook org-resolution paths that previously had no other way to
--                          resolve org_id without a bypass-holding role.
--
--   review_iq_admin      -- BYPASSRLS. Requires its OWN distinct Secret Manager secret,
--                          deployed ONLY to the review-iq-admin Cloud Run service's
--                          ADMIN_DATABASE_URL env var, REPLACING the current (accidental)
--                          reuse of supabase-database-url. This is an infrastructure step
--                          this migration cannot perform -- see the cutover runbook.
--
-- Webhook org-resolution no longer needs ANY bypass-holding role: two SECURITY DEFINER
-- functions resolve location_name/shop_domain -> org_id (and return NOTHING else -- not the
-- row, not the encrypted refresh token, just the org_id) under the function OWNER's
-- privileges. The CALLING role (review_iq_app, no bypass after this migration) only needs
-- EXECUTE on the function, not SELECT on the underlying table and certainly not BYPASSRLS.

-- ---------------------------------------------------------------------------
-- 1. review_iq_migrator -- schema-scoped DDL/DML role, not superuser
-- ---------------------------------------------------------------------------

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'review_iq_migrator') THEN
    CREATE ROLE review_iq_migrator LOGIN;
  END IF;
END
$$;

ALTER ROLE review_iq_migrator BYPASSRLS;
GRANT CONNECT ON DATABASE postgres TO review_iq_migrator;
GRANT ALL ON SCHEMA public TO review_iq_migrator;
GRANT ALL ON ALL TABLES IN SCHEMA public TO review_iq_migrator;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO review_iq_migrator;
GRANT ALL ON ALL FUNCTIONS IN SCHEMA public TO review_iq_migrator;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO review_iq_migrator;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO review_iq_migrator;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO review_iq_migrator;

-- The migration-executing role (postgres, per existing convention) must be a MEMBER of
-- review_iq_migrator before it can ALTER FUNCTION ... OWNER TO it below (Supabase's
-- `postgres` role here is NOT rolsuper=true -- verified directly -- so this grant is
-- required, not optional).
GRANT review_iq_migrator TO postgres;

-- Password generated and set out-of-band (never committed) -- see the cutover runbook.

-- ---------------------------------------------------------------------------
-- 2. review_iq_admin -- BYPASSRLS, used only by the (to-be-re-keyed) private admin
--    Cloud Run service
-- ---------------------------------------------------------------------------

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'review_iq_admin') THEN
    CREATE ROLE review_iq_admin LOGIN;
  END IF;
END
$$;

GRANT authenticated TO review_iq_admin;
GRANT CONNECT ON DATABASE postgres TO review_iq_admin;
ALTER ROLE review_iq_admin BYPASSRLS;

-- Password generated and set out-of-band; this MUST be a DIFFERENT Secret Manager secret
-- from review_iq_app's -- see the cutover runbook for why the admin service currently
-- reusing supabase-database-url is itself part of what this migration fixes.

-- ---------------------------------------------------------------------------
-- 3. Narrow SECURITY DEFINER lookups for webhook org-resolution -- replaces the need for
--    ANY bypass-holding role in app/api/webhooks/{google,shopify}.py.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.resolve_org_for_google_location(p_location_name text)
RETURNS uuid
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
STABLE
AS $$
  SELECT org_id
  FROM public.google_business_installations
  WHERE google_location_name = p_location_name
  LIMIT 1;
$$;

COMMENT ON FUNCTION public.resolve_org_for_google_location IS
  'BYPASSRLS remediation: narrow SECURITY DEFINER lookup replacing a bypass-holding role '
  'for Google Business Profile webhook org-resolution. Returns ONLY org_id (or NULL) -- '
  'never the row, never refresh_token_enc. Owned by review_iq_migrator (non-superuser, '
  'schema-scoped -- SECURITY DEFINER here means bypassing RLS for exactly this one query, '
  'not database-wide access).';

ALTER FUNCTION public.resolve_org_for_google_location OWNER TO review_iq_migrator;
-- This project's pg_default_acl grants EXECUTE on every new public-schema function
-- (created by postgres) to anon/authenticated/service_role automatically -- a bare
-- REVOKE ALL FROM PUBLIC does NOT remove those, since they're direct default-ACL grants,
-- not PUBLIC's own entry. Leaving `authenticated` granted would let any logged-in
-- dashboard customer (or, for `anon`, anyone holding the necessarily-public anon key) call
-- this cross-tenant org_id lookup directly via /rest/v1/rpc/..., bypassing webhook
-- signature verification entirely. The only legitimate caller is review_iq_app's own
-- direct psycopg2 connection, never exposed via PostgREST.
REVOKE ALL ON FUNCTION public.resolve_org_for_google_location FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.resolve_org_for_google_location TO review_iq_app;

CREATE OR REPLACE FUNCTION public.resolve_org_for_shopify_shop(p_shop_domain text)
RETURNS uuid
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
STABLE
AS $$
  SELECT org_id
  FROM public.shopify_installations
  WHERE shop_domain = p_shop_domain
  LIMIT 1;
$$;

COMMENT ON FUNCTION public.resolve_org_for_shopify_shop IS
  'BYPASSRLS remediation: narrow SECURITY DEFINER lookup replacing a bypass-holding role '
  'for Shopify webhook org-resolution. Returns ONLY org_id (or NULL) -- never the row, '
  'never the encrypted OAuth token. Owned by review_iq_migrator, same reasoning as '
  'resolve_org_for_google_location above.';

ALTER FUNCTION public.resolve_org_for_shopify_shop OWNER TO review_iq_migrator;
REVOKE ALL ON FUNCTION public.resolve_org_for_shopify_shop FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.resolve_org_for_shopify_shop TO review_iq_app;

-- ---------------------------------------------------------------------------
-- 4. Revoke BYPASSRLS from review_iq_app -- THE core fix. Must be the LAST statement
--    applied, after the code changes that stop relying on it are already deployed (a
--    sequencing requirement, not a database-logic one) -- see the cutover runbook.
-- ---------------------------------------------------------------------------

ALTER ROLE review_iq_app NOBYPASSRLS;

-- ---------------------------------------------------------------------------
-- 5. api_keys.key_prefix -- UNIQUE constraint (found during the same remediation pass,
--    not part of the original PR #33 attempt). key_prefix is riq_live_ + 8 hex chars =
--    32 bits of entropy -- meaningfully collision-prone at scale (birthday bound ~65k
--    keys for a 50% collision chance). Every current lookup site (app/auth/api_key.py's
--    `WHERE key_prefix = %s FOR UPDATE`) assumed uniqueness that was never actually
--    enforced -- a real collision would have `fetchone()` silently return whichever of
--    the two matching rows sorts first, and correctness rested entirely on the
--    downstream argon2id verification catching the mismatch (which it does -- this was
--    a latent availability bug, not a security exposure -- but the fix belongs at the
--    schema layer, not as a documented assumption). Existing rows are assumed
--    collision-free (verified: this table has well under 65k rows in production);
--    if this ever fails on real data, the actual duplicate key_prefix must be
--    investigated, not the constraint relaxed.
-- ---------------------------------------------------------------------------

ALTER TABLE public.api_keys ADD CONSTRAINT api_keys_key_prefix_key UNIQUE (key_prefix);

-- ---------------------------------------------------------------------------
-- 6. organization_members.user_id -- UNIQUE constraint. The table's primary key is the
--    composite (org_id, user_id), so `user_id` alone was never guaranteed unique --
--    every current lookup (app/auth/session.py, app/auth/signup.py) does
--    `WHERE user_id = %s [LIMIT 1]` assuming exactly one row. The product does not
--    support a user belonging to more than one org (no invite/team-join flow exists
--    anywhere in this codebase; app/auth/signup.py's _provision_org_and_key is the only
--    path that inserts into this table, and it always creates a brand-new org). The only
--    way a user_id could ever get a second row was a race in that same signup path (two
--    concurrent /auth/provision calls for the same brand-new user, both observing "no
--    existing org" before either commits) -- this constraint closes that race at the
--    schema layer; the accompanying code change (app/auth/signup.py) now catches the
--    resulting UniqueViolation and returns the winning org gracefully instead of a raw
--    500. If multi-org membership is ever a real product feature, this constraint must
--    be dropped and every WHERE user_id = %s call site re-audited for which org it
--    actually means -- do not silently work around it before then.
-- ---------------------------------------------------------------------------

ALTER TABLE public.organization_members ADD CONSTRAINT organization_members_user_id_key UNIQUE (user_id);
