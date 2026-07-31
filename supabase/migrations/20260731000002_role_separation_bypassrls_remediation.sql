-- Migration: role separation to close the BYPASSRLS reachability finding (S0, Wave 1
-- post-remediation pass). NOT applied by this session -- see the PR body for the exact
-- cutover sequence GG must run this in, since order matters (see bottom of this file).
--
-- Prior state (20260726000001_review_iq_app_role.sql): review_iq_app held BYPASSRLS and
-- was the ONLY app-facing role, used by every code path including 3 identified
-- request-serving paths that never call _set_tenant() (app/api/admin.py's org/key CRUD,
-- app/api/webhooks/{google,shopify}.py's org-resolution lookups). RLS provided zero
-- protection to those 3 paths, and the role reachable from EVERY ordinary
-- extraction/query request also held bypass -- correctness rested entirely on every
-- current and future function remembering to call _set_tenant() first, with no
-- database-level backstop if one didn't.
--
-- New topology -- three roles, each with the minimum privilege its one job needs:
--
--   review_iq_migrator  -- BYPASSRLS, broad DML/DDL on the `public` schema ONLY (not
--                          superuser -- no access to this shared Supabase project's
--                          other schemas, e.g. the sibling `expense` app's schema, unlike
--                          the `postgres` superuser migrations currently run as). LOGIN,
--                          but its password is generated and set out-of-band, same
--                          pattern review_iq_app's own migration already used, and it is
--                          NEVER referenced by any application setting, Cloud Run env
--                          var, or Secret Manager secret used by the deployed app --
--                          reachability from a request-serving path is proven by
--                          grepping every DSN/connection-string construction in app/ and
--                          scripts/ (see the accompanying PR's verification section),
--                          not asserted.
--
--   review_iq_app        -- BYPASSRLS REMOVED (this migration's core fix). Continues to
--                          be the only role SUPABASE_DATABASE_URL (the deployed public
--                          Cloud Run service) authenticates as. Every tenant-scoped query
--                          already calls _set_tenant() before touching tenant data
--                          (verified: 20/21 functions in app/core/storage_pg.py; the 1
--                          exception, list_orgs_with_dated_extractions_pg, returns only
--                          org_ids -- no per-org content -- and is reachable only from an
--                          internal cron sweep, never a public request; unaffected by
--                          this change since RLS on `extractions` doesn't restrict a
--                          DISTINCT org_id projection any differently once bypass is
--                          removed -- SELECT DISTINCT org_id still needs its own
--                          justification comment, already present, not a bypass grant).
--
--   review_iq_admin      -- BYPASSRLS. Used ONLY by a NEW, separate, IAM-gated Cloud Run
--                          service (not the public one) that mounts ONLY app/api/admin.py's
--                          org/key CRUD routes -- see docs/architecture/adr/0006 for the
--                          service-split design. Its credential is a distinct Secret
--                          Manager secret, never present in the public service's
--                          environment.
--
-- Webhook org-resolution no longer needs ANY bypass-holding role: two SECURITY DEFINER
-- functions below resolve location_name/shop_domain -> org_id (and return NOTHING else --
-- not the row, not the encrypted refresh token, just the org_id) under the function
-- OWNER's privileges. The CALLING role (review_iq_app, no bypass after this migration)
-- only needs EXECUTE grant on the function, not SELECT on the underlying table and
-- certainly not BYPASSRLS -- this is the standard minimum-privilege pattern for "one
-- narrow cross-tenant lookup" that this repo's own prior BYPASSRLS design comment
-- (20260726000001) flagged as the alternative to a blanket bypass grant, without using it
-- until now.

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

-- The migration-executing role (postgres, per existing convention -- see
-- ops/runbooks/secret-rotation.md) must be a MEMBER of review_iq_migrator before it can
-- ALTER FUNCTION ... OWNER TO it below (standard Postgres rule: ownership transfer
-- requires membership in the target role unless the current role is a true superuser --
-- Supabase's `postgres` role here is NOT rolsuper=true, verified directly, so this grant
-- is required, not optional -- caught by dry-running this migration before asking GG to
-- apply it for real).
GRANT review_iq_migrator TO postgres;

-- NOTE (documented limitation, not silently overclaimed): this grant set covers every
-- DDL/DML operation this repo's migrations have needed to date (CREATE/ALTER TABLE,
-- CREATE POLICY, CREATE FUNCTION, CREATE INDEX). It is NOT schema-ownership-equivalent --
-- if a future migration needs a privilege this role lacks, it will fail loudly
-- (permission denied) rather than silently, and the fix is an additional GRANT in that
-- migration, not reverting to the `postgres` superuser for convenience.

-- The actual password is generated and set out-of-band (never committed), same
-- convention as review_iq_app -- see ops/runbooks/secret-rotation.md.

-- ---------------------------------------------------------------------------
-- 2. review_iq_admin -- BYPASSRLS, used only by the new private admin Cloud Run service
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

-- Same "does not grant access beyond what it was already granted" property already
-- documented for review_iq_app in 20260726000001 -- BYPASSRLS only affects row-level
-- security filtering on tables the role already has a base GRANT on via `authenticated`
-- membership, all of which are already scoped to this app's own tables in `public`.

-- Password generated and set out-of-band; this is a DIFFERENT Secret Manager secret from
-- review_iq_app's, deployed ONLY as an env var on the new private admin Cloud Run
-- service -- never on the public one.

-- ---------------------------------------------------------------------------
-- 3. Narrow SECURITY DEFINER lookups for webhook org-resolution -- replaces the need
--    for ANY bypass-holding role in app/api/webhooks/{google,shopify}.py.
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
  'Wave 1 S0 remediation: narrow SECURITY DEFINER lookup replacing a bypass-holding role '
  'for Google Business Profile webhook org-resolution. Returns ONLY org_id (or NULL) --  '
  'never the row, never refresh_token_enc. Owned by review_iq_migrator (a non-superuser, '
  'schema-scoped role -- SECURITY DEFINER functions run with their owner''s privileges, '
  'which here means bypassing RLS for exactly this one query, not database-wide access).';

ALTER FUNCTION public.resolve_org_for_google_location OWNER TO review_iq_migrator;
-- Found via this migration's own dry-run verification, not assumed: this project's
-- pg_default_acl grants EXECUTE on every new `public`-schema function (created by
-- `postgres`) to anon/authenticated/service_role automatically -- a REVOKE ALL FROM
-- PUBLIC alone does NOT remove those, since they're direct default-ACL grants, not
-- PUBLIC's own entry. `authenticated` is Supabase's PostgREST role -- leaving it granted
-- would let any logged-in dashboard customer (or, for `anon`, anyone holding the
-- necessarily-public anon key) call this cross-tenant org_id lookup directly via
-- `/rest/v1/rpc/...`, bypassing the webhook signature-verification path entirely. The
-- only legitimate caller is review_iq_app's own direct psycopg2 connection (never
-- exposed via PostgREST, which only ever switches into anon/authenticated/service_role).
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
  'Wave 1 S0 remediation: narrow SECURITY DEFINER lookup replacing a bypass-holding role '
  'for Shopify webhook org-resolution. Returns ONLY org_id (or NULL) -- never the row, '
  'never the encrypted OAuth token. Owned by review_iq_migrator, same reasoning as '
  'resolve_org_for_google_location above.';

ALTER FUNCTION public.resolve_org_for_shopify_shop OWNER TO review_iq_migrator;
-- Same default-ACL leak and same fix as resolve_org_for_google_location above.
REVOKE ALL ON FUNCTION public.resolve_org_for_shopify_shop FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.resolve_org_for_shopify_shop TO review_iq_app;

-- ---------------------------------------------------------------------------
-- 4. Revoke BYPASSRLS from review_iq_app -- THE core fix. See PR body for why this must
--    be the LAST statement applied, after the code changes that stop relying on it are
--    already deployed (sequencing, not a database-logic requirement).
-- ---------------------------------------------------------------------------

ALTER ROLE review_iq_app NOBYPASSRLS;
