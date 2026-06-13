-- Migration: authenticity_audits table for IS 19000:2022 compliance audit trail.
-- Pattern matches existing tenant tables: same current_org_id() helper, same
-- USING + WITH CHECK on authenticated, anon deny.
--
-- NOTE: public.current_org_id() is already defined in 20260510000002_rls_policies.sql.
-- This migration does NOT redefine it.

-- ---------------------------------------------------------------------------
-- Table
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.authenticity_audits (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID NOT NULL,
    review_hash TEXT NOT NULL,
    score       REAL NOT NULL,
    label       TEXT NOT NULL,
    flags       TEXT NOT NULL DEFAULT '[]',   -- JSON array
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_authenticity_audits_org_created
    ON public.authenticity_audits (org_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Row-Level Security
-- ---------------------------------------------------------------------------
ALTER TABLE public.authenticity_audits ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  DROP POLICY IF EXISTS "authenticity_audits_authenticated_all" ON public.authenticity_audits;
  DROP POLICY IF EXISTS "authenticity_audits_anon_deny"         ON public.authenticity_audits;
END $$;

CREATE POLICY "authenticity_audits_authenticated_all" ON public.authenticity_audits
  FOR ALL TO authenticated
  USING     (org_id = public.current_org_id())
  WITH CHECK (org_id = public.current_org_id());

CREATE POLICY "authenticity_audits_anon_deny" ON public.authenticity_audits
  FOR ALL TO anon USING (false);
