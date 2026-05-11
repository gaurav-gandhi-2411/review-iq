-- Migration: add flat queryable columns to extractions table.
-- The original extraction column is made nullable since flat columns carry the data.
-- The original schema stored all LLM output in a single jsonb column;
-- flat columns allow efficient indexing and filtering without jsonb extraction.
-- The old extraction column is left intact for backwards compatibility.

-- Make original extraction jsonb column optional now that flat columns carry the data
ALTER TABLE public.extractions ALTER COLUMN extraction DROP NOT NULL;

ALTER TABLE public.extractions
  ADD COLUMN IF NOT EXISTS review_text          text,
  ADD COLUMN IF NOT EXISTS product              text,
  ADD COLUMN IF NOT EXISTS stars                integer,
  ADD COLUMN IF NOT EXISTS stars_inferred       integer,
  ADD COLUMN IF NOT EXISTS buy_again            boolean,
  ADD COLUMN IF NOT EXISTS sentiment            text,
  ADD COLUMN IF NOT EXISTS urgency              text        NOT NULL DEFAULT 'low',
  ADD COLUMN IF NOT EXISTS language             text        NOT NULL DEFAULT 'en',
  ADD COLUMN IF NOT EXISTS review_length_chars  integer,
  ADD COLUMN IF NOT EXISTS confidence           real,
  ADD COLUMN IF NOT EXISTS topics               jsonb       NOT NULL DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS competitor_mentions  jsonb       NOT NULL DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS pros                 jsonb       NOT NULL DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS cons                 jsonb       NOT NULL DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS feature_requests     jsonb       NOT NULL DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS extracted_at         timestamptz;

-- Unique constraint for cache deduplication: same org, same content hash = same extraction
DO $$ BEGIN
  BEGIN
    ALTER TABLE public.extractions
      ADD CONSTRAINT extractions_org_input_hash_unique UNIQUE (org_id, input_hash);
  EXCEPTION WHEN duplicate_object THEN NULL;
  END;
END $$;

-- Indexes for common v2 filter queries
CREATE INDEX IF NOT EXISTS idx_extractions_sentiment_org
  ON public.extractions (org_id, sentiment);

CREATE INDEX IF NOT EXISTS idx_extractions_urgency_org
  ON public.extractions (org_id, urgency);

CREATE INDEX IF NOT EXISTS idx_extractions_product_org
  ON public.extractions (org_id, product);
