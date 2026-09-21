-- Migration: add tokens_in / tokens_out to usage_records.
-- tokens_used becomes a generated column (tokens_in + tokens_out) for
-- backward compatibility with any query that reads the original column.

ALTER TABLE public.usage_records
  ADD COLUMN IF NOT EXISTS tokens_in  integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS tokens_out integer NOT NULL DEFAULT 0;

-- Recreate tokens_used as a generated column.
-- Must drop the plain column first (PostgreSQL does not support converting
-- a regular column to a generated one in-place).
ALTER TABLE public.usage_records DROP COLUMN IF EXISTS tokens_used;

ALTER TABLE public.usage_records
  ADD COLUMN tokens_used integer GENERATED ALWAYS AS (tokens_in + tokens_out) STORED;

-- ---------------------------------------------------------------------------
-- Postconditions (Session 15d) -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py. Each holds against the FINAL
-- schema state (a later migration must not undo it), in the CI build and in production.
-- ---------------------------------------------------------------------------
-- @postcondition: usage_records_token_columns
-- SQL: SELECT (SELECT count(*) FROM pg_attribute a WHERE a.attrelid =
-- SQL: 'public.usage_records'::regclass AND a.attnum > 0 AND NOT a.attisdropped AND (a.attname,
-- SQL: format_type(a.atttypid, a.atttypmod), a.attnotnull) IN (('tokens_in', 'integer', true),
-- SQL: ('tokens_out', 'integer', true))) = 2 AND EXISTS (SELECT 1 FROM pg_attribute a WHERE
-- SQL: a.attrelid = 'public.usage_records'::regclass AND a.attname = 'tokens_used' AND NOT
-- SQL: a.attisdropped AND a.attgenerated = 's' AND format_type(a.atttypid, a.atttypmod) =
-- SQL: 'integer')
