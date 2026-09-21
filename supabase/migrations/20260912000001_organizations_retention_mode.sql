-- Migration: per-org retention_mode (stateless default | retained) + retention_days window.
-- Session 12 P2a. Schema change -- STOP for GG per the autonomy contract. NOT APPLIED by this
-- session; reported here for review. See docs/architecture/adr/0025-two-modes-retention-schema.md
-- for the full design and why these specific defaults/constraints were chosen.
--
-- Hits the table-ownership wall reported in Session 11 (H3d): public.organizations is owned by
-- `postgres`, not `review_iq_migrator` -- this ALTER TABLE will fail with
-- "must be owner of table organizations" via the normal migration pipeline until GG's parallel
-- ownership-transfer work (P7c) lands. Apply via the Supabase SQL Editor as the postgres
-- superuser, same as Session 11's extraction_costs migration, OR re-run supabase/push.py once
-- ownership is transferred.

ALTER TABLE public.organizations
    ADD COLUMN IF NOT EXISTS retention_mode TEXT NOT NULL DEFAULT 'stateless'
        CHECK (retention_mode IN ('stateless', 'retained'));

ALTER TABLE public.organizations
    ADD COLUMN IF NOT EXISTS retention_days INTEGER
        CHECK (retention_days IS NULL OR retention_days IN (30, 90));

DO $$ BEGIN
    ALTER TABLE public.organizations
        ADD CONSTRAINT organizations_retention_consistent
        CHECK (
            (retention_mode = 'stateless' AND retention_days IS NULL) OR
            (retention_mode = 'retained'  AND retention_days IS NOT NULL)
        );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- Narrow SECURITY DEFINER cross-org lister for the purge job (P2c), same pattern as
-- list_orgs_with_daily_digest (20260817000003_cross_org_sweep_resolvers.sql): review_iq_app
-- holds no BYPASSRLS and no direct cross-org SELECT on organizations, so the purge job
-- (app/core/retention.py) needs a narrow function that returns ONLY what it needs (org_id,
-- retention_days) to compute each org's cutoff, never any other organizations column.
CREATE OR REPLACE FUNCTION public.list_orgs_with_retained_mode()
RETURNS TABLE(org_id uuid, retention_days integer)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
STABLE
AS $$
  SELECT o.id, o.retention_days
  FROM public.organizations o
  WHERE o.retention_mode = 'retained';
$$;

COMMENT ON FUNCTION public.list_orgs_with_retained_mode IS
  'Session 12 P2c: narrow SECURITY DEFINER cross-org lister for the retention-window purge '
  'job (app/core/retention.py). Returns ONLY org_id + retention_days -- never name, slug, '
  'plan, or any other organizations column. Same reasoning as list_orgs_with_daily_digest.';

ALTER FUNCTION public.list_orgs_with_retained_mode OWNER TO review_iq_migrator;
REVOKE ALL ON FUNCTION public.list_orgs_with_retained_mode FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.list_orgs_with_retained_mode TO review_iq_app;

-- Every existing org defaults to 'stateless' with this migration (D1: stateless is the default,
-- not merely the default for NEW orgs). VERIFIED SAFE before writing this migration: queried
-- every row in public.organizations (18 rows) and public.api_keys (11 rows) directly -- every
-- single one is a test/demo/dev artifact from prior sessions (SmokeTest, Alpha/Beta, Eval,
-- Public Demo, Batch Row Org A/B, Audit a/b, gaurav-dev, GG's own gg5678g@gmail.com dogfooding
-- org) or an unrevoked key named "default"/"dev-test"/"demo-cassette-key" -- no real paying
-- customer exists yet (matches D5's pre-launch framing). Defaulting everything to stateless
-- breaks no one. This verification will NOT hold true forever -- re-check before ever re-running
-- a migration with this same "safe because nothing real depends on it yet" reasoning once a
-- real customer has signed up.

-- ---------------------------------------------------------------------------
-- Postconditions (Session 15d) -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py. Each holds against the FINAL
-- schema state (a later migration must not undo it), in the CI build and in production.
-- ---------------------------------------------------------------------------
-- @postcondition: organizations_retention_columns
-- SQL: SELECT (SELECT count(*) FROM pg_attribute a WHERE a.attrelid =
-- SQL: 'public.organizations'::regclass AND a.attnum > 0 AND NOT a.attisdropped AND (a.attname,
-- SQL: format_type(a.atttypid, a.atttypmod), a.attnotnull) IN (('retention_mode', 'text', true),
-- SQL: ('retention_days', 'integer', false))) = 2 AND EXISTS (SELECT 1 FROM pg_constraint c WHERE
-- SQL: c.conrelid = 'public.organizations'::regclass AND c.contype = 'c' AND
-- SQL: pg_get_constraintdef(c.oid) LIKE '%stateless%' AND pg_get_constraintdef(c.oid) LIKE
-- SQL: '%retained%') AND EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conrelid =
-- SQL: 'public.organizations'::regclass AND c.contype = 'c' AND pg_get_constraintdef(c.oid) LIKE
-- SQL: '%retention_days%' AND pg_get_constraintdef(c.oid) LIKE '%30%' AND
-- SQL: pg_get_constraintdef(c.oid) LIKE '%90%')

-- @postcondition: organizations_retention_consistent_constraint
-- SQL: SELECT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conrelid =
-- SQL: 'public.organizations'::regclass AND c.conname = 'organizations_retention_consistent' AND
-- SQL: c.contype = 'c' AND pg_get_constraintdef(c.oid) LIKE '%retention_mode%' AND
-- SQL: pg_get_constraintdef(c.oid) LIKE '%retention_days IS NULL%')

-- @postcondition: retained_mode_lister_is_hardened_security_definer
-- SQL: SELECT count(*) = 1 AND bool_and(p.prosecdef AND pg_get_userbyid(p.proowner) =
-- SQL: 'review_iq_migrator' AND p.proconfig @> ARRAY['search_path=public'] AND
-- SQL: has_function_privilege('review_iq_app', p.oid, 'EXECUTE') AND NOT
-- SQL: has_function_privilege('anon', p.oid, 'EXECUTE') AND NOT
-- SQL: has_function_privilege('authenticated', p.oid, 'EXECUTE') AND NOT
-- SQL: has_function_privilege('service_role', p.oid, 'EXECUTE')) FROM pg_proc p WHERE p.oid IN
-- SQL: (to_regprocedure('public.list_orgs_with_retained_mode()'))
