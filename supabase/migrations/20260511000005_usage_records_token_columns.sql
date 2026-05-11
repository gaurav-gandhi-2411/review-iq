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
