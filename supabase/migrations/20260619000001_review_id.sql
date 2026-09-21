-- Migration: canonical review_id — bridges the extractions / authenticity_audits hash gap.
--
-- extractions.input_hash  = "sha256:<64-char-hex>"  (prefixed format from ReviewRequest)
-- authenticity_audits.review_hash = "<64-char-hex>" (plain hex from AuthenticityResult)
--
-- review_id = sha256 hex of review text, no prefix — the intersection value between both
-- existing columns. GENERATED ALWAYS AS STORED: computed from existing column, zero
-- backfill UPDATE needed, zero insert-path changes, no drift possible.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS; CREATE INDEX IF NOT EXISTS.

ALTER TABLE public.extractions
    ADD COLUMN IF NOT EXISTS review_id TEXT
    GENERATED ALWAYS AS (REPLACE(input_hash, 'sha256:', '')) STORED;

ALTER TABLE public.authenticity_audits
    ADD COLUMN IF NOT EXISTS review_id TEXT
    GENERATED ALWAYS AS (review_hash) STORED;

CREATE INDEX IF NOT EXISTS idx_extractions_org_review_id
    ON public.extractions (org_id, review_id);

CREATE INDEX IF NOT EXISTS idx_authenticity_audits_org_review_id
    ON public.authenticity_audits (org_id, review_id);

-- ---------------------------------------------------------------------------
-- Postconditions (Session 15d) -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py. Each holds against the FINAL
-- schema state (a later migration must not undo it), in the CI build and in production.
-- ---------------------------------------------------------------------------
-- @postcondition: review_id_generated_columns
-- SQL: SELECT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid =
-- SQL: 'public.extractions'::regclass AND a.attname = 'review_id' AND NOT a.attisdropped AND
-- SQL: a.attgenerated = 's' AND format_type(a.atttypid, a.atttypmod) = 'text') AND EXISTS (SELECT 1
-- SQL: FROM pg_attribute a WHERE a.attrelid = 'public.authenticity_audits'::regclass AND a.attname
-- SQL: = 'review_id' AND NOT a.attisdropped AND a.attgenerated = 's' AND format_type(a.atttypid,
-- SQL: a.atttypmod) = 'text')

-- @postcondition: review_id_indexes
-- SQL: SELECT EXISTS (SELECT 1 FROM pg_index i WHERE i.indexrelid =
-- SQL: to_regclass('public.idx_extractions_org_review_id') AND i.indrelid =
-- SQL: 'public.extractions'::regclass) AND EXISTS (SELECT 1 FROM pg_index i WHERE i.indexrelid =
-- SQL: to_regclass('public.idx_authenticity_audits_org_review_id') AND i.indrelid =
-- SQL: 'public.authenticity_audits'::regclass)
