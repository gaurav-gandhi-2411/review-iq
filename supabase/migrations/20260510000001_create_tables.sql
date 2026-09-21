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

-- ---------------------------------------------------------------------------
-- Postconditions (Session 15d) -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py. Each holds against the FINAL
-- schema state (a later migration must not undo it), in the CI build and in production.
-- ---------------------------------------------------------------------------
-- @postcondition: core_tables_exist
-- SQL: SELECT to_regclass('public.organizations') IS NOT NULL AND to_regclass('public.api_keys') IS
-- SQL: NOT NULL AND to_regclass('public.extractions') IS NOT NULL AND
-- SQL: to_regclass('public.usage_records') IS NOT NULL AND
-- SQL: to_regclass('public.organization_members') IS NOT NULL

-- @postcondition: organizations_columns
-- SQL: SELECT (SELECT count(*) FROM pg_attribute a WHERE a.attrelid =
-- SQL: 'public.organizations'::regclass AND a.attnum > 0 AND NOT a.attisdropped AND (a.attname,
-- SQL: format_type(a.atttypid, a.atttypmod), a.attnotnull) IN (('id', 'uuid', true), ('name',
-- SQL: 'text', true), ('slug', 'text', true), ('plan', 'text', true), ('created_at',
-- SQL: 'timestamp with time zone', true))) = 5

-- @postcondition: organizations_slug_unique_and_plan_check
-- SQL: SELECT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conrelid =
-- SQL: 'public.organizations'::regclass AND c.conname = 'organizations_slug_key' AND c.contype =
-- SQL: 'u' AND pg_get_constraintdef(c.oid) = 'UNIQUE (slug)') AND EXISTS (SELECT 1 FROM
-- SQL: pg_constraint c WHERE c.conrelid = 'public.organizations'::regclass AND c.contype = 'c' AND
-- SQL: pg_get_constraintdef(c.oid) LIKE '%enterprise%') AND EXISTS (SELECT 1 FROM pg_constraint c
-- SQL: WHERE c.conrelid = 'public.organizations'::regclass AND c.contype = 'p' AND
-- SQL: pg_get_constraintdef(c.oid) = 'PRIMARY KEY (id)')

-- @postcondition: api_keys_columns_and_unique_hash
-- SQL: SELECT (SELECT count(*) FROM pg_attribute a WHERE a.attrelid = 'public.api_keys'::regclass
-- SQL: AND a.attnum > 0 AND NOT a.attisdropped AND (a.attname, format_type(a.atttypid,
-- SQL: a.atttypmod), a.attnotnull) IN (('id', 'uuid', true), ('org_id', 'uuid', true), ('key_hash',
-- SQL: 'text', true), ('name', 'text', true), ('quota', 'integer', true), ('created_at',
-- SQL: 'timestamp with time zone', true), ('last_used_at', 'timestamp with time zone', false))) = 7
-- SQL: AND EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conrelid = 'public.api_keys'::regclass AND
-- SQL: c.conname = 'api_keys_key_hash_key' AND c.contype = 'u' AND pg_get_constraintdef(c.oid) =
-- SQL: 'UNIQUE (key_hash)') AND EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conrelid =
-- SQL: 'public.api_keys'::regclass AND c.contype = 'c' AND pg_get_constraintdef(c.oid) LIKE
-- SQL: '%quota > 0%')

-- @postcondition: extractions_original_columns
-- SQL: SELECT (SELECT count(*) FROM pg_attribute a WHERE a.attrelid =
-- SQL: 'public.extractions'::regclass AND a.attnum > 0 AND NOT a.attisdropped AND (a.attname,
-- SQL: format_type(a.atttypid, a.atttypmod), a.attnotnull) IN (('id', 'uuid', true), ('org_id',
-- SQL: 'uuid', true), ('api_key_id', 'uuid', false), ('input_hash', 'text', true), ('model',
-- SQL: 'text', true), ('prompt_version', 'text', true), ('schema_version', 'text', true),
-- SQL: ('latency_ms', 'integer', false), ('is_suspicious', 'boolean', true), ('created_at',
-- SQL: 'timestamp with time zone', true))) = 10

-- @postcondition: usage_records_and_members_columns
-- SQL: SELECT (SELECT count(*) FROM pg_attribute a WHERE a.attrelid =
-- SQL: 'public.usage_records'::regclass AND a.attnum > 0 AND NOT a.attisdropped AND (a.attname,
-- SQL: format_type(a.atttypid, a.atttypmod), a.attnotnull) IN (('id', 'uuid', true), ('org_id',
-- SQL: 'uuid', true), ('api_key_id', 'uuid', false), ('created_at', 'timestamp with time zone',
-- SQL: true))) = 4 AND (SELECT count(*) FROM pg_attribute a WHERE a.attrelid =
-- SQL: 'public.organization_members'::regclass AND a.attnum > 0 AND NOT a.attisdropped AND
-- SQL: (a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull) IN (('org_id', 'uuid',
-- SQL: true), ('user_id', 'uuid', true), ('role', 'text', true), ('created_at',
-- SQL: 'timestamp with time zone', true))) = 4 AND EXISTS (SELECT 1 FROM pg_constraint c WHERE
-- SQL: c.conrelid = 'public.organization_members'::regclass AND c.contype = 'p' AND
-- SQL: pg_get_constraintdef(c.oid) = 'PRIMARY KEY (org_id, user_id)') AND EXISTS (SELECT 1 FROM
-- SQL: pg_constraint c WHERE c.conrelid = 'public.organization_members'::regclass AND c.contype =
-- SQL: 'c' AND pg_get_constraintdef(c.oid) LIKE '%owner%')

-- @postcondition: tenant_foreign_keys
-- SQL: SELECT (SELECT count(*) FROM pg_constraint c WHERE c.contype = 'f' AND c.confrelid =
-- SQL: 'public.organizations'::regclass AND c.confdeltype = 'c' AND c.conrelid IN
-- SQL: ('public.api_keys'::regclass, 'public.extractions'::regclass,
-- SQL: 'public.usage_records'::regclass, 'public.organization_members'::regclass)) = 4 AND (SELECT
-- SQL: count(*) FROM pg_constraint c WHERE c.contype = 'f' AND c.confrelid =
-- SQL: 'public.api_keys'::regclass AND c.confdeltype = 'n' AND c.conrelid IN
-- SQL: ('public.extractions'::regclass, 'public.usage_records'::regclass)) = 2

-- @postcondition: original_indexes_exist
-- SQL: SELECT EXISTS (SELECT 1 FROM pg_index i WHERE i.indexrelid =
-- SQL: to_regclass('public.idx_api_keys_org_id') AND i.indrelid = 'public.api_keys'::regclass) AND
-- SQL: EXISTS (SELECT 1 FROM pg_index i WHERE i.indexrelid =
-- SQL: to_regclass('public.idx_extractions_org_id') AND i.indrelid =
-- SQL: 'public.extractions'::regclass) AND EXISTS (SELECT 1 FROM pg_index i WHERE i.indexrelid =
-- SQL: to_regclass('public.idx_extractions_created_at') AND i.indrelid =
-- SQL: 'public.extractions'::regclass) AND EXISTS (SELECT 1 FROM pg_index i WHERE i.indexrelid =
-- SQL: to_regclass('public.idx_usage_records_org_id') AND i.indrelid =
-- SQL: 'public.usage_records'::regclass) AND EXISTS (SELECT 1 FROM pg_index i WHERE i.indexrelid =
-- SQL: to_regclass('public.idx_usage_records_created_at') AND i.indrelid =
-- SQL: 'public.usage_records'::regclass) AND EXISTS (SELECT 1 FROM pg_index i WHERE i.indexrelid =
-- SQL: to_regclass('public.idx_org_members_user_id') AND i.indrelid =
-- SQL: 'public.organization_members'::regclass)
