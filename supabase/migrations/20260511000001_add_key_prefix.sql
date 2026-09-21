-- Migration: add key_prefix to api_keys for O(1) candidate lookup.
-- Idempotent: ADD COLUMN IF NOT EXISTS, CREATE INDEX IF NOT EXISTS.
--
-- Pattern: key_prefix (first 17 chars of riq_live_<hex>) is stored indexed.
-- Auth flow: WHERE key_prefix = ? → single candidate → argon2id.verify(key_hash, raw_key).
-- key_prefix exposes 8 hex chars (32 bits); remaining 24 hex chars (96 bits) stay secret.
-- Collision probability is negligible at any realistic key count per org.

ALTER TABLE public.api_keys
  ADD COLUMN IF NOT EXISTS key_prefix text NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_api_keys_key_prefix
  ON public.api_keys (key_prefix);

-- ---------------------------------------------------------------------------
-- Postconditions (Session 15d) -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py. Each holds against the FINAL
-- schema state (a later migration must not undo it), in the CI build and in production.
-- ---------------------------------------------------------------------------
-- @postcondition: api_keys_key_prefix_column
-- SQL: SELECT (SELECT count(*) FROM pg_attribute a WHERE a.attrelid = 'public.api_keys'::regclass
-- SQL: AND a.attnum > 0 AND NOT a.attisdropped AND (a.attname, format_type(a.atttypid,
-- SQL: a.atttypmod), a.attnotnull) IN (('key_prefix', 'text', true))) = 1

-- @postcondition: api_keys_key_prefix_index
-- SQL: SELECT EXISTS (SELECT 1 FROM pg_index i WHERE i.indexrelid =
-- SQL: to_regclass('public.idx_api_keys_key_prefix') AND i.indrelid = 'public.api_keys'::regclass)
