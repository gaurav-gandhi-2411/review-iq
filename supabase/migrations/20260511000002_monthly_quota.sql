-- Rename usage (lifetime counter) to monthly_usage.
-- Quota enforcement now derives monthly count from usage_records via date_trunc;
-- this column is retained for admin reporting only and is no longer incremented.
ALTER TABLE public.api_keys
  RENAME COLUMN usage TO monthly_usage;
