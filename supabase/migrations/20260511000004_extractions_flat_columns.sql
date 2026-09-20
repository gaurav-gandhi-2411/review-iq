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

-- ---------------------------------------------------------------------------
-- Postconditions (Session 15d) -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py. Each holds against the FINAL
-- schema state (a later migration must not undo it), in the CI build and in production.
-- ---------------------------------------------------------------------------
-- @postcondition: extractions_flat_columns
-- SQL: SELECT (SELECT count(*) FROM pg_attribute a WHERE a.attrelid =
-- SQL: 'public.extractions'::regclass AND a.attnum > 0 AND NOT a.attisdropped AND (a.attname,
-- SQL: format_type(a.atttypid, a.atttypmod), a.attnotnull) IN (('review_text', 'text', false),
-- SQL: ('product', 'text', false), ('stars', 'integer', false), ('stars_inferred', 'integer',
-- SQL: false), ('buy_again', 'boolean', false), ('sentiment', 'text', false), ('urgency', 'text',
-- SQL: true), ('language', 'text', true), ('review_length_chars', 'integer', false), ('confidence',
-- SQL: 'real', false), ('topics', 'jsonb', true), ('competitor_mentions', 'jsonb', true), ('pros',
-- SQL: 'jsonb', true), ('cons', 'jsonb', true), ('feature_requests', 'jsonb', true),
-- SQL: ('extracted_at', 'timestamp with time zone', false))) = 16

-- @postcondition: extraction_jsonb_column_is_nullable
-- SQL: SELECT (SELECT count(*) FROM pg_attribute a WHERE a.attrelid =
-- SQL: 'public.extractions'::regclass AND a.attnum > 0 AND NOT a.attisdropped AND (a.attname,
-- SQL: format_type(a.atttypid, a.atttypmod), a.attnotnull) IN (('extraction', 'jsonb', false))) = 1

-- @postcondition: extractions_org_input_hash_unique_and_indexes
-- SQL: SELECT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conrelid =
-- SQL: 'public.extractions'::regclass AND c.conname = 'extractions_org_input_hash_unique' AND
-- SQL: c.contype = 'u' AND pg_get_constraintdef(c.oid) = 'UNIQUE (org_id, input_hash)') AND EXISTS
-- SQL: (SELECT 1 FROM pg_index i WHERE i.indexrelid =
-- SQL: to_regclass('public.idx_extractions_sentiment_org') AND i.indrelid =
-- SQL: 'public.extractions'::regclass) AND EXISTS (SELECT 1 FROM pg_index i WHERE i.indexrelid =
-- SQL: to_regclass('public.idx_extractions_urgency_org') AND i.indrelid =
-- SQL: 'public.extractions'::regclass) AND EXISTS (SELECT 1 FROM pg_index i WHERE i.indexrelid =
-- SQL: to_regclass('public.idx_extractions_product_org') AND i.indrelid =
-- SQL: 'public.extractions'::regclass)
