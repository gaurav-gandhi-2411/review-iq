-- Migration: initial schema for review-iq multi-tenant platform
-- Idempotent: all DDL uses IF NOT EXISTS / OR REPLACE.

-- ---------------------------------------------------------------------------
-- organizations
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.organizations (
  id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  name       text        NOT NULL,
  slug       text        NOT NULL,
  plan       text        NOT NULL DEFAULT 'free' CHECK (plan IN ('free', 'pro', 'enterprise')),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT organizations_slug_key UNIQUE (slug)
);

-- ---------------------------------------------------------------------------
-- api_keys  (key stored as argon2id hash, never plaintext)
-- Format: riq_live_<32-char-hex>  — argon2id(raw_key) in key_hash, raw key returned once on creation
-- Lookup: key_prefix (first 17 chars) indexed for O(1) candidate lookup; argon2id.verify confirms.
-- See migration 20260511000001 for the key_prefix column addition.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.api_keys (
  id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       uuid        NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  key_hash     text        NOT NULL,
  name         text        NOT NULL,
  quota        integer     NOT NULL DEFAULT 1000 CHECK (quota > 0),
  usage        integer     NOT NULL DEFAULT 0    CHECK (usage >= 0),
  created_at   timestamptz NOT NULL DEFAULT now(),
  last_used_at timestamptz,
  CONSTRAINT api_keys_key_hash_key UNIQUE (key_hash)
);

-- ---------------------------------------------------------------------------
-- extractions
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.extractions (
  id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid        NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  api_key_id     uuid        REFERENCES public.api_keys (id) ON DELETE SET NULL,
  input_hash     text        NOT NULL,  -- SHA-256 of sanitised review text
  extraction     jsonb       NOT NULL,
  model          text        NOT NULL,
  prompt_version text        NOT NULL,
  schema_version text        NOT NULL,
  latency_ms     integer,
  is_suspicious  boolean     NOT NULL DEFAULT false,
  created_at     timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- usage_records
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.usage_records (
  id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      uuid        NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  api_key_id  uuid        REFERENCES public.api_keys (id) ON DELETE SET NULL,
  tokens_used integer     NOT NULL DEFAULT 0 CHECK (tokens_used >= 0),
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- organization_members  (stub for Phase 2.0b+ user auth)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.organization_members (
  org_id     uuid        NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
  user_id    uuid        NOT NULL,  -- will reference auth.users in Phase 2.0b
  role       text        NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'admin', 'member')),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, user_id)
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_api_keys_org_id          ON public.api_keys (org_id);
CREATE INDEX IF NOT EXISTS idx_extractions_org_id        ON public.extractions (org_id);
CREATE INDEX IF NOT EXISTS idx_extractions_created_at    ON public.extractions (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_usage_records_org_id      ON public.usage_records (org_id);
CREATE INDEX IF NOT EXISTS idx_usage_records_created_at  ON public.usage_records (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_org_members_user_id       ON public.organization_members (user_id);

-- ---------------------------------------------------------------------------
-- Permissions: authenticated role must be able to DML on these tables.
-- service_role bypasses RLS entirely (no grants needed for it).
-- ---------------------------------------------------------------------------
GRANT USAGE ON SCHEMA public TO authenticated, anon;
GRANT SELECT, INSERT, UPDATE, DELETE
  ON public.organizations, public.api_keys, public.extractions,
     public.usage_records, public.organization_members
  TO authenticated;
-- anon gets nothing — all access must be via API key + service_role
