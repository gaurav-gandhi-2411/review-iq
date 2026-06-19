-- Migration: corrections table — per-org labeled correction flywheel.
--
-- Captures user corrections to extraction/authenticity/reply artifacts.
-- Corrections are CANDIDATES only — never auto-applied to model/prompts/gold-set.
-- Field-path validation is enforced at the API layer (app/core/corrections/schema.py);
-- source_type CHECK enforces the same values at the DB layer.
--
-- RLS: WITH CHECK is mandatory — prevents INSERT of a correction tagged with a
-- different org's org_id (the batch_jobs hole where INSERT bypasses USING alone).
--
-- Idempotent: CREATE TABLE / INDEX use IF NOT EXISTS; policy block drops before recreating.

-- ---------------------------------------------------------------------------
-- Table
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.corrections (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id           UUID        NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
    review_id        TEXT        NOT NULL,  -- sha256 hex; joins extractions.review_id / authenticity_audits.review_id
    source_type      TEXT        NOT NULL CHECK (source_type IN ('extraction', 'authenticity', 'reply')),
    field_path       TEXT        NOT NULL,  -- validated against ALLOWED_FIELD_PATHS in app/core/corrections/schema.py
    original_value   TEXT        NOT NULL,
    corrected_value  TEXT        NOT NULL,
    correction_note  TEXT,
    language         TEXT        NOT NULL DEFAULT 'en',
    corrected_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_corrections_org_corrected_at
    ON public.corrections (org_id, corrected_at DESC);

CREATE INDEX IF NOT EXISTS idx_corrections_org_review_id
    ON public.corrections (org_id, review_id);

-- ---------------------------------------------------------------------------
-- Grants (authenticated role; service_role bypasses RLS and needs no grant)
-- ---------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE, DELETE ON public.corrections TO authenticated;

-- ---------------------------------------------------------------------------
-- Row-Level Security
-- ---------------------------------------------------------------------------
ALTER TABLE public.corrections ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    DROP POLICY IF EXISTS "corrections_authenticated_all" ON public.corrections;
    DROP POLICY IF EXISTS "corrections_anon_deny"         ON public.corrections;
END $$;

-- WITH CHECK mandatory: INSERT bypasses USING; omitting it lets a tenant write
-- corrections under another org's org_id while still reading only their own rows.
CREATE POLICY "corrections_authenticated_all" ON public.corrections
    FOR ALL TO authenticated
    USING     (org_id = public.current_org_id())
    WITH CHECK (org_id = public.current_org_id());

CREATE POLICY "corrections_anon_deny" ON public.corrections
    FOR ALL TO anon USING (false);
