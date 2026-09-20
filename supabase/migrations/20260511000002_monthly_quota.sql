-- Rename usage (lifetime counter) to monthly_usage.
-- Quota enforcement now derives monthly count from usage_records via date_trunc;
-- this column is retained for admin reporting only and is no longer incremented.
ALTER TABLE public.api_keys
  RENAME COLUMN usage TO monthly_usage;

-- ---------------------------------------------------------------------------
-- Postconditions (Session 15d) -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py. Each holds against the FINAL
-- schema state (a later migration must not undo it), in the CI build and in production.
-- ---------------------------------------------------------------------------
-- @postcondition: usage_column_renamed_to_monthly_usage
-- SQL: SELECT (SELECT count(*) FROM pg_attribute a WHERE a.attrelid = 'public.api_keys'::regclass
-- SQL: AND a.attnum > 0 AND NOT a.attisdropped AND (a.attname, format_type(a.atttypid,
-- SQL: a.atttypmod), a.attnotnull) IN (('monthly_usage', 'integer', true))) = 1 AND NOT EXISTS
-- SQL: (SELECT 1 FROM pg_attribute a WHERE a.attrelid = 'public.api_keys'::regclass AND a.attname =
-- SQL: 'usage' AND NOT a.attisdropped)
