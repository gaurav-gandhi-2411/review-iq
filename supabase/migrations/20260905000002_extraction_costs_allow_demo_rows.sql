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
