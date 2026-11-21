-- Migration: classified_reviews -- reviews the encoder-first router skipped (S29, gate 4, NOT APPLIED to production).
--
-- Why: with ENABLE_ENCODER_FIRST on (spec Amendment 14), a review the encoder judges low-risk gets no LLM call at ingest. Today the ingest
-- worker simply drops it after the queue row settles. This table keeps what is needed to extract it later when a user opens it: the
-- review text (only while it is still unextracted), the encoder's decisions, and a link to the extraction once one exists.
--
-- Expand-only: one new table, no existing object is altered. Nothing writes to it until ENABLE_ENCODER_FIRST is turned on (flag OFF).
--
-- Data minimisation: `review_text` is held ONLY while status = 'classified_only' (CHECK below); extracting a review sets it NULL, so the
-- raw text lives no longer than the queue row it replaces (batch_job_rows.text) plus the time until the user opens the review. It is
-- removed by the retention purge and by POST /v2/purge like extractions (app/core/storage_pg.py), and by the organizations ON DELETE
-- CASCADE. ReviewRequest caps text at 5000 characters, mirrored here.
--
-- Access model: same as extractions -- RLS enabled, FOR ALL to authenticated scoped by org_id = current_org_id(), anon denied, and the
-- command-level grants narrowed to SELECT/INSERT/UPDATE/DELETE (no TRUNCATE/REFERENCES/TRIGGER). The app reaches it through
-- SET LOCAL ROLE authenticated + app.current_org_id, never as a superuser and never cross-org.
--
-- Idempotent replay: UNIQUE (org_id, input_hash); a re-queued identical review inserts nothing (ON CONFLICT DO NOTHING in the app).
--
-- Rollback: DROP TABLE public.classified_reviews. It holds only derived classifications plus unextracted review text that is still in
-- the customer's own source; extractions made from it stay in public.extractions (extraction_id is ON DELETE SET NULL).

CREATE TABLE IF NOT EXISTS public.classified_reviews (
    id                uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id            uuid         NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
    input_hash        text         NOT NULL CHECK (char_length(input_hash) BETWEEN 8 AND 128),
    review_text       text         CHECK (review_text IS NULL OR char_length(review_text) BETWEEN 1 AND 5000),
    review_date       timestamptz,
    product           text         CHECK (product IS NULL OR char_length(product) <= 500),
    classification    jsonb        NOT NULL,
    thresholds_sha256 text         NOT NULL CHECK (thresholds_sha256 ~ '^[0-9a-f]{64}$'),
    status            text         NOT NULL DEFAULT 'classified_only' CHECK (status IN ('classified_only', 'extracted')),
    extraction_id     uuid         REFERENCES public.extractions (id) ON DELETE SET NULL,
    created_at        timestamptz  NOT NULL DEFAULT now(),
    extracted_at      timestamptz,
    CONSTRAINT classified_reviews_org_hash_key UNIQUE (org_id, input_hash),
    CONSTRAINT classified_reviews_text_only_while_unextracted
        CHECK ((status = 'classified_only') = (review_text IS NOT NULL)),
    CONSTRAINT classified_reviews_extracted_at_matches_status
        CHECK ((status = 'extracted') = (extracted_at IS NOT NULL))
);

-- The "not yet analysed" list for one org, newest first.
CREATE INDEX IF NOT EXISTS classified_reviews_org_pending_idx
    ON public.classified_reviews (org_id, created_at DESC) WHERE status = 'classified_only';

ALTER TABLE public.classified_reviews ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.classified_reviews FROM PUBLIC, anon, authenticated, service_role, review_iq_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.classified_reviews TO authenticated;

DROP POLICY IF EXISTS "classified_reviews_authenticated_all" ON public.classified_reviews;
DROP POLICY IF EXISTS "classified_reviews_anon_deny" ON public.classified_reviews;

CREATE POLICY "classified_reviews_authenticated_all" ON public.classified_reviews
    FOR ALL TO authenticated
    USING      (org_id = public.current_org_id())
    WITH CHECK (org_id = public.current_org_id());

CREATE POLICY "classified_reviews_anon_deny" ON public.classified_reviews
    FOR ALL TO anon USING (false);

-- ---------------------------------------------------------------------------
-- Postconditions -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py.
-- ---------------------------------------------------------------------------
-- @postcondition: classified_reviews_columns_and_unique_org_hash
-- SQL: SELECT (SELECT count(*) FROM pg_attribute a WHERE a.attrelid =
-- SQL: 'public.classified_reviews'::regclass AND a.attnum > 0 AND NOT a.attisdropped AND
-- SQL: (a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull) IN (('id', 'uuid', true),
-- SQL: ('org_id', 'uuid', true), ('input_hash', 'text', true), ('review_text', 'text', false),
-- SQL: ('review_date', 'timestamp with time zone', false), ('product', 'text', false),
-- SQL: ('classification', 'jsonb', true), ('thresholds_sha256', 'text', true),
-- SQL: ('status', 'text', true), ('extraction_id', 'uuid', false),
-- SQL: ('created_at', 'timestamp with time zone', true),
-- SQL: ('extracted_at', 'timestamp with time zone', false))) = 12 AND
-- SQL: EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conrelid = 'public.classified_reviews'::regclass
-- SQL: AND c.conname = 'classified_reviews_org_hash_key' AND c.contype = 'u')

-- @postcondition: classified_reviews_text_only_while_unextracted_constraint
-- SQL: SELECT count(*) = 2 FROM pg_constraint c WHERE c.conrelid = 'public.classified_reviews'::regclass
-- SQL: AND c.contype = 'c' AND c.conname IN ('classified_reviews_text_only_while_unextracted',
-- SQL: 'classified_reviews_extracted_at_matches_status')

-- @postcondition: classified_reviews_rls_enabled_with_org_and_anon_policies
-- SQL: SELECT (SELECT relrowsecurity FROM pg_class WHERE oid = 'public.classified_reviews'::regclass)
-- SQL: AND (SELECT count(*) FROM pg_policies p WHERE p.schemaname = 'public' AND
-- SQL: p.tablename = 'classified_reviews' AND p.cmd = 'ALL' AND
-- SQL: ((p.policyname = 'classified_reviews_authenticated_all' AND p.roles = '{authenticated}') OR
-- SQL: (p.policyname = 'classified_reviews_anon_deny' AND p.roles = '{anon}'))) = 2 AND
-- SQL: (SELECT count(*) FROM pg_policies p WHERE p.schemaname = 'public' AND
-- SQL: p.tablename = 'classified_reviews') = 2

-- @postcondition: classified_reviews_grants_select_insert_update_delete_to_authenticated_only
-- SQL: SELECT count(*) = 7 AND bool_and((has_table_privilege('authenticated', 'public.classified_reviews', p)
-- SQL: = (p IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE')))
-- SQL: AND NOT has_table_privilege('anon', 'public.classified_reviews', p)
-- SQL: AND NOT has_table_privilege('service_role', 'public.classified_reviews', p))
-- SQL: FROM unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) AS p
