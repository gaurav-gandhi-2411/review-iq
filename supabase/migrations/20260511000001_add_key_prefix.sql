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
