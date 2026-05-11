-- Migration: Row-Level Security for all tenant tables.
-- Idempotent: policies are dropped and recreated; RLS enable is idempotent.
--
-- Isolation model:
--   service_role  → bypasses RLS (trusted app connection)
--   authenticated → RLS enforced via current_org_id() helper
--   anon          → denied on all tenant tables
--
-- Org context resolution order (current_org_id function):
--   1. request.jwt.claims.org_id  — set by PostgREST from Bearer JWT
--   2. app.current_org_id          — set by app via SET LOCAL (direct psycopg2)
--   Returns NULL if neither is set → RLS denies all access.

-- ---------------------------------------------------------------------------
-- Helper: resolve current org from request context
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.current_org_id()
  RETURNS uuid
  LANGUAGE plpgsql STABLE SECURITY DEFINER
  AS $$
  DECLARE
    v_claims text;
    v_org_id text;
  BEGIN
    -- Path 1: JWT claims set by PostgREST
    v_claims := current_setting('request.jwt.claims', true);
    IF v_claims IS NOT NULL AND v_claims <> '' THEN
      BEGIN
        v_org_id := (v_claims::jsonb) ->> 'org_id';
        IF v_org_id IS NOT NULL AND v_org_id <> '' THEN
          RETURN v_org_id::uuid;
        END IF;
      EXCEPTION WHEN OTHERS THEN
        NULL;  -- malformed JSON — fall through
      END;
    END IF;

    -- Path 2: direct DB connection (integration tests, background workers)
    v_org_id := current_setting('app.current_org_id', true);
    IF v_org_id IS NOT NULL AND v_org_id <> '' THEN
      BEGIN
        RETURN v_org_id::uuid;
      EXCEPTION WHEN OTHERS THEN
        NULL;
      END;
    END IF;

    RETURN NULL;
  END;
  $$;

-- ---------------------------------------------------------------------------
-- Enable RLS
-- ---------------------------------------------------------------------------
ALTER TABLE public.organizations         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.api_keys              ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.extractions           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.usage_records         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.organization_members  ENABLE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- Drop existing policies before recreating (idempotency)
-- ---------------------------------------------------------------------------
DO $$ BEGIN
  DROP POLICY IF EXISTS "orgs_authenticated_all"        ON public.organizations;
  DROP POLICY IF EXISTS "orgs_anon_deny"                ON public.organizations;
  DROP POLICY IF EXISTS "api_keys_authenticated_all"    ON public.api_keys;
  DROP POLICY IF EXISTS "api_keys_anon_deny"            ON public.api_keys;
  DROP POLICY IF EXISTS "extractions_authenticated_all" ON public.extractions;
  DROP POLICY IF EXISTS "extractions_anon_deny"         ON public.extractions;
  DROP POLICY IF EXISTS "usage_authenticated_all"       ON public.usage_records;
  DROP POLICY IF EXISTS "usage_anon_deny"               ON public.usage_records;
  DROP POLICY IF EXISTS "members_authenticated_all"     ON public.organization_members;
  DROP POLICY IF EXISTS "members_anon_deny"             ON public.organization_members;
END $$;

-- ---------------------------------------------------------------------------
-- organizations
-- ---------------------------------------------------------------------------
CREATE POLICY "orgs_authenticated_all" ON public.organizations
  FOR ALL TO authenticated
  USING     (id = public.current_org_id())
  WITH CHECK (id = public.current_org_id());

CREATE POLICY "orgs_anon_deny" ON public.organizations
  FOR ALL TO anon USING (false);

-- ---------------------------------------------------------------------------
-- api_keys
-- ---------------------------------------------------------------------------
CREATE POLICY "api_keys_authenticated_all" ON public.api_keys
  FOR ALL TO authenticated
  USING     (org_id = public.current_org_id())
  WITH CHECK (org_id = public.current_org_id());

CREATE POLICY "api_keys_anon_deny" ON public.api_keys
  FOR ALL TO anon USING (false);

-- ---------------------------------------------------------------------------
-- extractions
-- ---------------------------------------------------------------------------
CREATE POLICY "extractions_authenticated_all" ON public.extractions
  FOR ALL TO authenticated
  USING     (org_id = public.current_org_id())
  WITH CHECK (org_id = public.current_org_id());

CREATE POLICY "extractions_anon_deny" ON public.extractions
  FOR ALL TO anon USING (false);

-- ---------------------------------------------------------------------------
-- usage_records
-- ---------------------------------------------------------------------------
CREATE POLICY "usage_authenticated_all" ON public.usage_records
  FOR ALL TO authenticated
  USING     (org_id = public.current_org_id())
  WITH CHECK (org_id = public.current_org_id());

CREATE POLICY "usage_anon_deny" ON public.usage_records
  FOR ALL TO anon USING (false);

-- ---------------------------------------------------------------------------
-- organization_members
-- ---------------------------------------------------------------------------
-- TODO (Phase 2.5 dashboard auth): this policy scopes members by org_id, which
-- works for the API-key flow (service_role sets org context before querying).
-- A dashboard user needs to query "which orgs am I a member of?" without an
-- org context already set — that requires a separate policy scoped by user_id
-- (e.g. USING (user_id = auth.uid())) or a combined (org_id = current_org_id()
-- OR user_id = auth.uid()) expression. Revisit when Supabase Auth is wired in.
CREATE POLICY "members_authenticated_all" ON public.organization_members
  FOR ALL TO authenticated
  USING     (org_id = public.current_org_id())
  WITH CHECK (org_id = public.current_org_id());

CREATE POLICY "members_anon_deny" ON public.organization_members
  FOR ALL TO anon USING (false);
