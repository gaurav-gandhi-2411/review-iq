-- Soft-delete support for api_keys: revoked keys are retained for audit purposes.
-- _lookup_and_record filters revoked_at IS NULL so revoked keys cannot authenticate.
ALTER TABLE public.api_keys
  ADD COLUMN IF NOT EXISTS revoked_at timestamptz;
