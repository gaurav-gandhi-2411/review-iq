-- Migration: allow public.extraction_costs to record keyless /demo/extract calls.
--
-- Why: the 2026-09-05 state-reconstruction audit found extraction_costs' org_id NOT
-- NULL constraint structurally prevents it from ever recording the demo endpoint's
-- cost, even once app code writes to the table -- there is no org on that path by
-- design. Fixed here: org_id becomes nullable, a `source` discriminator makes the two
-- row shapes ("org" rows always carry org_id; "demo" rows never do) an enforced
-- invariant rather than a convention, and a narrow new RLS policy lets ONLY
-- review_iq_app (the app's own runtime role, not anon/authenticated) insert a
-- null-org "demo" row. Every existing policy, grant, and index on this table is
-- untouched -- authenticated tenants still see only their own org's rows (a NULL
-- org_id never equals any real current_org_id(), so demo rows are invisible to every
-- tenant automatically, with no policy change needed for that direction).

ALTER TABLE public.extraction_costs
    ALTER COLUMN org_id DROP NOT NULL;

ALTER TABLE public.extraction_costs
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'org';

DO $$ BEGIN
    ALTER TABLE public.extraction_costs
        ADD CONSTRAINT extraction_costs_source_org_id_consistent
        CHECK (
            (source = 'org'  AND org_id IS NOT NULL) OR
            (source = 'demo' AND org_id IS NULL)
        );
EXCEPTION
    WHEN duplicate_object THEN NULL;  -- idempotent re-run
END $$;

-- review_iq_app may insert a "demo" row (org_id NULL) directly -- it is a distinct
-- Postgres role from anon/authenticated (member of authenticated, per
-- 20260726000001_review_iq_app_role.sql, but this policy is scoped to review_iq_app
-- specifically, not broadened to every authenticated-role member) and this is the
-- only role app/core/storage_pg.py::record_demo_extraction_cost_pg ever connects as.
-- Existing "extraction_costs_authenticated_all" (org_id = current_org_id()) and
-- "extraction_costs_anon_deny" (USING false) policies are untouched; RLS policies are
-- OR'd together for permissive policies, so this is purely additive.
DO $$ BEGIN
    DROP POLICY IF EXISTS "extraction_costs_review_iq_app_demo_insert" ON public.extraction_costs;
END $$;

CREATE POLICY "extraction_costs_review_iq_app_demo_insert" ON public.extraction_costs
    FOR INSERT TO review_iq_app
    WITH CHECK (source = 'demo' AND org_id IS NULL);

-- ---------------------------------------------------------------------------
-- Postconditions (Session 15d) -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py. Each holds against the FINAL
-- schema state (a later migration must not undo it), in the CI build and in production.
-- ---------------------------------------------------------------------------
-- @postcondition: extraction_costs_org_id_nullable_and_source_column
-- SQL: SELECT (SELECT count(*) FROM pg_attribute a WHERE a.attrelid =
-- SQL: 'public.extraction_costs'::regclass AND a.attnum > 0 AND NOT a.attisdropped AND (a.attname,
-- SQL: format_type(a.atttypid, a.atttypmod), a.attnotnull) IN (('org_id', 'uuid', false))) = 1 AND
-- SQL: (SELECT count(*) FROM pg_attribute a WHERE a.attrelid = 'public.extraction_costs'::regclass
-- SQL: AND a.attnum > 0 AND NOT a.attisdropped AND (a.attname, format_type(a.atttypid,
-- SQL: a.atttypmod), a.attnotnull) IN (('source', 'text', true))) = 1 AND EXISTS (SELECT 1 FROM
-- SQL: pg_constraint c WHERE c.conrelid = 'public.extraction_costs'::regclass AND c.conname =
-- SQL: 'extraction_costs_source_org_id_consistent' AND c.contype = 'c' AND
-- SQL: pg_get_constraintdef(c.oid) LIKE '%demo%' AND pg_get_constraintdef(c.oid) LIKE
-- SQL: '%org_id IS NULL%')

-- @postcondition: extraction_costs_demo_insert_policy_is_review_iq_app_only
-- SQL: SELECT (SELECT count(*) FROM pg_policies p WHERE p.schemaname = 'public' AND (p.tablename,
-- SQL: p.policyname, p.cmd, p.roles::text) IN (('extraction_costs',
-- SQL: 'extraction_costs_review_iq_app_demo_insert', 'INSERT', '{review_iq_app}'))) = 1 AND (SELECT
-- SQL: count(*) FROM pg_policies p WHERE p.schemaname = 'public' AND p.policyname =
-- SQL: 'extraction_costs_review_iq_app_demo_insert' AND p.with_check LIKE '%demo%' AND p.with_check
-- SQL: LIKE '%org_id IS NULL%') = 1
