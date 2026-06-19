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
