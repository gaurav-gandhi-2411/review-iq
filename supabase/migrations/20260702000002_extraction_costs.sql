-- Migration: backfill the CREATE TABLE this repo never actually had.
--
-- Found 2026-08-01 during a full schema-fidelity audit (P0) comparing production
-- directly against the schema every committed migration, applied in order, would
-- produce -- same class of gap as 20260710000002_quota_requests.sql, found by the
-- same method (a live diff, not a guess): public.extraction_costs exists in
-- production with no CREATE TABLE anywhere in supabase/migrations/. Schema
-- confirmed against the real production table directly (information_schema.columns,
-- pg_constraint, pg_indexes, pg_policies, information_schema.role_table_grants), not
-- guessed. Table's grants (ALL 7 privileges to authenticated, nothing to anon/
-- service_role/review_iq_migrator) match Supabase's platform default-privileges
-- mechanism exactly -- no explicit GRANT statement needed here, same as every other
-- table in this schema.
--
-- Dated 20260702000002 (same day as google_business_installations, the nearest
-- dated migration before this table's likely creation window based on column
-- design -- extraction_id/org_id FKs to tables that existed by then) -- the exact
-- original creation date is unrecoverable; this is a reasonable placement, not a
-- claim of historical accuracy.

CREATE TABLE IF NOT EXISTS public.extraction_costs (
  id             uuid            PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid            NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  extraction_id  uuid            REFERENCES public.extractions (id) ON DELETE SET NULL,
  provider       text            NOT NULL,
  model          text            NOT NULL,
  tier           text            NOT NULL,
  language       text,
  tokens_in      integer         NOT NULL DEFAULT 0 CHECK (tokens_in >= 0),
  tokens_out     integer         NOT NULL DEFAULT 0 CHECK (tokens_out >= 0),
  cost_usd       numeric(14, 8)  NOT NULL CHECK (cost_usd >= 0),
  cost_inr       numeric(14, 6)  NOT NULL CHECK (cost_inr >= 0),
  created_at     timestamptz     NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_extraction_costs_org_created_at
  ON public.extraction_costs (org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_extraction_costs_language_tier
  ON public.extraction_costs (language, tier);

ALTER TABLE public.extraction_costs ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  DROP POLICY IF EXISTS "extraction_costs_authenticated_all" ON public.extraction_costs;
  DROP POLICY IF EXISTS "extraction_costs_anon_deny"         ON public.extraction_costs;
END $$;

CREATE POLICY "extraction_costs_authenticated_all" ON public.extraction_costs
  FOR ALL TO authenticated
  USING     (org_id = public.current_org_id())
  WITH CHECK (org_id = public.current_org_id());

CREATE POLICY "extraction_costs_anon_deny" ON public.extraction_costs
  FOR ALL TO anon USING (false);
