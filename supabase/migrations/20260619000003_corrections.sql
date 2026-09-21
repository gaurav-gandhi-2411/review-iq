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

-- ---------------------------------------------------------------------------
-- Postconditions (Session 15d) -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py. Each holds against the FINAL
-- schema state (a later migration must not undo it), in the CI build and in production.
-- ---------------------------------------------------------------------------
-- @postcondition: corrections_columns_and_constraints
-- SQL: SELECT (SELECT count(*) FROM pg_attribute a WHERE a.attrelid =
-- SQL: 'public.corrections'::regclass AND a.attnum > 0 AND NOT a.attisdropped AND (a.attname,
-- SQL: format_type(a.atttypid, a.atttypmod), a.attnotnull) IN (('id', 'uuid', true), ('org_id',
-- SQL: 'uuid', true), ('review_id', 'text', true), ('source_type', 'text', true), ('field_path',
-- SQL: 'text', true), ('original_value', 'text', true), ('corrected_value', 'text', true),
-- SQL: ('correction_note', 'text', false), ('language', 'text', true), ('corrected_at',
-- SQL: 'timestamp with time zone', true))) = 10 AND EXISTS (SELECT 1 FROM pg_constraint c WHERE
-- SQL: c.conrelid = 'public.corrections'::regclass AND c.contype = 'f' AND c.confrelid =
-- SQL: 'public.organizations'::regclass AND c.confdeltype = 'c') AND EXISTS (SELECT 1 FROM
-- SQL: pg_constraint c WHERE c.conrelid = 'public.corrections'::regclass AND c.contype = 'c' AND
-- SQL: pg_get_constraintdef(c.oid) LIKE '%authenticity%' AND pg_get_constraintdef(c.oid) LIKE
-- SQL: '%reply%')

-- @postcondition: corrections_indexes
-- SQL: SELECT EXISTS (SELECT 1 FROM pg_index i WHERE i.indexrelid =
-- SQL: to_regclass('public.idx_corrections_org_corrected_at') AND i.indrelid =
-- SQL: 'public.corrections'::regclass) AND EXISTS (SELECT 1 FROM pg_index i WHERE i.indexrelid =
-- SQL: to_regclass('public.idx_corrections_org_review_id') AND i.indrelid =
-- SQL: 'public.corrections'::regclass)

-- @postcondition: corrections_rls_and_policies
-- SQL: SELECT (SELECT count(*) = 1 AND bool_and(c.relrowsecurity) FROM pg_class c WHERE c.oid IN
-- SQL: ('public.corrections'::regclass)) AND (SELECT count(*) FROM pg_policies p WHERE p.schemaname
-- SQL: = 'public' AND (p.tablename, p.policyname, p.cmd, p.roles::text) IN (('corrections',
-- SQL: 'corrections_authenticated_all', 'ALL', '{authenticated}'), ('corrections',
-- SQL: 'corrections_anon_deny', 'ALL', '{anon}'))) = 2 AND (SELECT count(*) FROM pg_policies p
-- SQL: WHERE p.schemaname = 'public' AND p.policyname IN ('corrections_authenticated_all') AND
-- SQL: p.qual LIKE '%current_org_id()%' AND p.with_check LIKE '%current_org_id()%') = 1 AND (SELECT
-- SQL: count(*) FROM pg_policies p WHERE p.schemaname = 'public' AND p.policyname IN
-- SQL: ('corrections_anon_deny') AND p.qual = 'false') = 1
