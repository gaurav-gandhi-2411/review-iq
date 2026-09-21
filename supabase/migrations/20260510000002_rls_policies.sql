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

-- ---------------------------------------------------------------------------
-- Postconditions (Session 15d) -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py. Each holds against the FINAL
-- schema state (a later migration must not undo it), in the CI build and in production.
-- ---------------------------------------------------------------------------
-- @postcondition: current_org_id_is_stable_security_definer_returning_uuid
-- SQL: SELECT EXISTS (SELECT 1 FROM pg_proc p WHERE p.oid =
-- SQL: to_regprocedure('public.current_org_id()') AND p.prosecdef AND p.provolatile = 's' AND
-- SQL: p.prorettype = 'uuid'::regtype)

-- @postcondition: rls_enabled_on_original_tenant_tables
-- SQL: SELECT (SELECT count(*) = 5 AND bool_and(c.relrowsecurity) FROM pg_class c WHERE c.oid IN
-- SQL: ('public.organizations'::regclass, 'public.api_keys'::regclass,
-- SQL: 'public.extractions'::regclass, 'public.usage_records'::regclass,
-- SQL: 'public.organization_members'::regclass))

-- @postcondition: original_tenant_policies_exist
-- SQL: SELECT (SELECT count(*) FROM pg_policies p WHERE p.schemaname = 'public' AND (p.tablename,
-- SQL: p.policyname, p.cmd, p.roles::text) IN (('organizations', 'orgs_authenticated_all', 'ALL',
-- SQL: '{authenticated}'), ('organizations', 'orgs_anon_deny', 'ALL', '{anon}'), ('api_keys',
-- SQL: 'api_keys_authenticated_all', 'ALL', '{authenticated}'), ('api_keys', 'api_keys_anon_deny',
-- SQL: 'ALL', '{anon}'), ('extractions', 'extractions_authenticated_all', 'ALL',
-- SQL: '{authenticated}'), ('extractions', 'extractions_anon_deny', 'ALL', '{anon}'),
-- SQL: ('usage_records', 'usage_authenticated_all', 'ALL', '{authenticated}'), ('usage_records',
-- SQL: 'usage_anon_deny', 'ALL', '{anon}'), ('organization_members', 'members_authenticated_all',
-- SQL: 'ALL', '{authenticated}'), ('organization_members', 'members_anon_deny', 'ALL', '{anon}')))
-- SQL: = 10

-- @postcondition: original_tenant_policies_scope_by_org_or_deny
-- SQL: SELECT (SELECT count(*) FROM pg_policies p WHERE p.schemaname = 'public' AND p.policyname IN
-- SQL: ('orgs_authenticated_all', 'api_keys_authenticated_all', 'extractions_authenticated_all',
-- SQL: 'usage_authenticated_all', 'members_authenticated_all') AND p.qual LIKE '%current_org_id()%'
-- SQL: AND p.with_check LIKE '%current_org_id()%') = 5 AND (SELECT count(*) FROM pg_policies p
-- SQL: WHERE p.schemaname = 'public' AND p.policyname IN ('orgs_anon_deny', 'api_keys_anon_deny',
-- SQL: 'extractions_anon_deny', 'usage_anon_deny', 'members_anon_deny') AND p.qual = 'false') = 5
