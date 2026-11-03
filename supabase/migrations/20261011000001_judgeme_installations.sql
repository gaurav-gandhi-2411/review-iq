-- Migration: Judge.me connector storage -- judgeme_installations + judgeme_review_state, and four
-- narrow SECURITY DEFINER functions (docs/specs/judgeme-ingestion.md sections 3.2, 3.4, 4).
--
-- MIGRATION NOT APPLIED BY THE PR THAT ADDS THIS FILE: GG decides when. Nothing reads these
-- tables while ENABLE_JUDGEME_CONNECTOR is false (the default), so applying later is harmless and
-- applying earlier changes no behaviour. UNVERIFIED against a real Postgres: written to the
-- conventions of 20260622000001 / 20260801000002 / 20261009000003 and checked only by the static
-- guards (postcondition grammar, BYPASSRLS grep); run supabase/ci/apply_migrations_ci.py in the
-- ephemeral-PG job before applying anywhere.
--
-- judgeme_installations: one row per connected Judge.me shop. The merchant-pasted PRIVATE API token
--   (read/write at Judge.me, not rotatable by the merchant) is stored Fernet-encrypted, exactly like
--   shopify_installations.access_token_enc, under its own key (JUDGEME_TOKEN_ENCRYPTION_KEY).
--   UNIQUE(shop_domain): one shop -> one org. token_enc is wiped ('') on revoke/disconnect; a CHECK
--   forbids an active row without a token. shop_domain is re-validated here (SSRF defence in depth).
-- judgeme_review_state: per Judge.me review id -> content hash and the extractions cache key
--   (input_hash), so edits replace and deletions remove. NO reviewer identity, no email, no phone.
--   Only written for retained-mode orgs (ADR 0025).
--
-- RLS: authenticated reads its own org's rows (SELECT policy, org_id = current_org_id()).
--   Installation writes go ONLY through the SECURITY DEFINER functions below (authenticated has no
--   INSERT/UPDATE policy -- same reason as upsert_shopify_installation). State rows are written by
--   the sync job under _set_tenant(), so authenticated gets a per-org ALL policy there.
--   review_iq_app holds no table grant and no BYPASSRLS (ADR 0006).
--
-- Idempotent: IF NOT EXISTS / DROP POLICY IF EXISTS / CREATE OR REPLACE.
--
-- UNDO (manual, in this order; destroys connector state, tokens and sync history; extractions are
-- untouched. Do not run once real merchants are connected without exporting first):
--   DROP FUNCTION IF EXISTS public.list_due_judgeme_installations(integer);
--   DROP FUNCTION IF EXISTS public.revoke_judgeme_installation(uuid, uuid, text);
--   DROP FUNCTION IF EXISTS public.record_judgeme_sync(uuid, uuid, text, text, timestamptz,
--       timestamptz, boolean, jsonb, timestamptz, boolean);
--   DROP FUNCTION IF EXISTS public.upsert_judgeme_installation(uuid, text, text, text);
--   DROP TABLE IF EXISTS public.judgeme_review_state;
--   DROP TABLE IF EXISTS public.judgeme_installations;

CREATE TABLE IF NOT EXISTS public.judgeme_installations (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id               UUID        NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    shop_domain          TEXT        NOT NULL UNIQUE
                         CHECK (shop_domain ~ '^[a-z0-9][a-z0-9-]*\.myshopify\.com$'),
    -- Fernet ciphertext; '' only on a revoked/disconnected row (token wiped).
    token_enc            TEXT        NOT NULL,
    -- Which transport the probe found Judge.me accepts: header (X-Api-Token) or query (api_token).
    auth_transport       TEXT        NOT NULL DEFAULT 'header'
                         CHECK (auth_transport IN ('header', 'query')),
    installed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at           TIMESTAMPTZ,
    revoked_reason       TEXT
                         CHECK (revoked_reason IN ('token_rejected', 'shop_not_found', 'disconnected')),
    last_sync_at         TIMESTAMPTZ,
    last_sync_status     TEXT
                         CHECK (last_sync_status IN
                                ('ok', 'suspect', 'error', 'skipped_stateless', 'rate_limited')),
    -- Machine code only (e.g. judgeme_unreachable), truncated; never a token, URL or review text.
    last_sync_error      TEXT,
    last_sync_summary    JSONB,
    -- Client-side watermark (max created/updated seen) and the last COMPLETE full scan.
    watermark            TIMESTAMPTZ,
    last_full_scan_at    TIMESTAMPTZ,
    -- Set when a configured list order was contradicted by data: later runs are full scans.
    order_violation_at   TIMESTAMPTZ,
    consecutive_failures INTEGER     NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    -- Defers the next attempt (429 Retry-After above the in-run cap).
    next_attempt_at      TIMESTAMPTZ,
    CONSTRAINT judgeme_inst_active_has_token CHECK (revoked_at IS NOT NULL OR token_enc <> ''),
    CONSTRAINT judgeme_inst_revoked_has_reason CHECK (revoked_at IS NULL OR revoked_reason IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_judgeme_inst_due
    ON public.judgeme_installations (last_sync_at NULLS FIRST)
    WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_judgeme_inst_org_id ON public.judgeme_installations (org_id);

CREATE TABLE IF NOT EXISTS public.judgeme_review_state (
    installation_id   UUID        NOT NULL REFERENCES public.judgeme_installations(id) ON DELETE CASCADE,
    review_id         TEXT        NOT NULL CHECK (review_id ~ '^[0-9]+$'),
    org_id            UUID        NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    content_hash      TEXT        NOT NULL,
    -- 'sha256:<hex>' of the staged text = extractions.input_hash (the cache key to purge).
    input_hash        TEXT        NOT NULL,
    state             TEXT        NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'gone')),
    missing_strikes   SMALLINT    NOT NULL DEFAULT 0 CHECK (missing_strikes >= 0),
    source_created_at TIMESTAMPTZ,
    source_updated_at TIMESTAMPTZ,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (installation_id, review_id)
);

CREATE INDEX IF NOT EXISTS idx_judgeme_state_org_id ON public.judgeme_review_state (org_id);

REVOKE ALL ON public.judgeme_installations FROM PUBLIC, anon, authenticated;
REVOKE ALL ON public.judgeme_review_state FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.judgeme_installations TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.judgeme_review_state TO authenticated;

ALTER TABLE public.judgeme_installations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.judgeme_review_state ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    DROP POLICY IF EXISTS "judgeme_inst_authenticated_select" ON public.judgeme_installations;
    DROP POLICY IF EXISTS "judgeme_inst_anon_deny"            ON public.judgeme_installations;
    DROP POLICY IF EXISTS "judgeme_state_authenticated_all"   ON public.judgeme_review_state;
    DROP POLICY IF EXISTS "judgeme_state_anon_deny"           ON public.judgeme_review_state;
END $$;

CREATE POLICY "judgeme_inst_authenticated_select" ON public.judgeme_installations
    FOR SELECT TO authenticated
    USING (org_id = public.current_org_id());
CREATE POLICY "judgeme_inst_anon_deny" ON public.judgeme_installations
    FOR ALL TO anon USING (false);
CREATE POLICY "judgeme_state_authenticated_all" ON public.judgeme_review_state
    FOR ALL TO authenticated
    USING (org_id = public.current_org_id())
    WITH CHECK (org_id = public.current_org_id());
CREATE POLICY "judgeme_state_anon_deny" ON public.judgeme_review_state
    FOR ALL TO anon USING (false);

-- ---------------------------------------------------------------------------
-- Functions. org_id is ALWAYS caller-resolved from the verified JWT (connect/disconnect) or from
-- list_due_judgeme_installations (sweep) -- never taken from a request body.
-- ---------------------------------------------------------------------------

-- Connect / reconnect. Refuses to take over a shop that another org holds ACTIVE (the Shopify
-- upsert lets a later caller overwrite; a pasted token proves access to the shop, not ownership of
-- the Samidha org that connected it first). Re-assigning a REVOKED shop to a new org drops the old
-- org's state rows. Reconnecting forces a full scan (watermark and last_full_scan_at cleared).
CREATE OR REPLACE FUNCTION public.upsert_judgeme_installation(
  p_org_id uuid, p_shop_domain text, p_token_enc text, p_auth_transport text
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_id uuid;
  v_owner uuid;
  v_revoked timestamptz;
BEGIN
  SELECT id, org_id, revoked_at INTO v_id, v_owner, v_revoked
  FROM public.judgeme_installations WHERE shop_domain = p_shop_domain FOR UPDATE;
  IF FOUND THEN
    IF v_owner <> p_org_id AND v_revoked IS NULL THEN
      RAISE EXCEPTION 'shop_connected_elsewhere' USING ERRCODE = '23505';
    END IF;
    IF v_owner <> p_org_id THEN
      DELETE FROM public.judgeme_review_state WHERE installation_id = v_id;
    END IF;
    UPDATE public.judgeme_installations SET
      org_id = p_org_id, token_enc = p_token_enc, auth_transport = p_auth_transport,
      installed_at = now(), revoked_at = NULL, revoked_reason = NULL,
      last_sync_at = NULL, last_sync_status = NULL, last_sync_error = NULL,
      last_sync_summary = NULL, watermark = NULL, last_full_scan_at = NULL,
      order_violation_at = NULL, consecutive_failures = 0, next_attempt_at = NULL
    WHERE id = v_id;
    RETURN v_id;
  END IF;
  INSERT INTO public.judgeme_installations (org_id, shop_domain, token_enc, auth_transport)
  VALUES (p_org_id, p_shop_domain, p_token_enc, p_auth_transport)
  RETURNING id INTO v_id;
  RETURN v_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.record_judgeme_sync(
  p_installation_id uuid, p_org_id uuid, p_status text, p_error text,
  p_watermark timestamptz, p_last_full_scan_at timestamptz, p_order_violation boolean,
  p_summary jsonb, p_next_attempt_at timestamptz, p_failed boolean
)
RETURNS void
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  UPDATE public.judgeme_installations SET
    last_sync_at = now(),
    last_sync_status = p_status,
    last_sync_error = left(p_error, 200),
    last_sync_summary = p_summary,
    watermark = COALESCE(p_watermark, watermark),
    last_full_scan_at = COALESCE(p_last_full_scan_at, last_full_scan_at),
    order_violation_at = CASE WHEN p_order_violation THEN COALESCE(order_violation_at, now())
                              ELSE order_violation_at END,
    next_attempt_at = p_next_attempt_at,
    consecutive_failures = CASE WHEN p_failed THEN consecutive_failures + 1 ELSE 0 END
  WHERE id = p_installation_id AND org_id = p_org_id AND revoked_at IS NULL;
$$;

-- Revoke or disconnect: stamps revoked_at/reason and WIPES the ciphertext (a rejected or
-- disconnected token has no further use here). The caller deletes the state rows under _set_tenant().
CREATE OR REPLACE FUNCTION public.revoke_judgeme_installation(
  p_installation_id uuid, p_org_id uuid, p_reason text
)
RETURNS void
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  UPDATE public.judgeme_installations SET
    revoked_at = COALESCE(revoked_at, now()),
    revoked_reason = COALESCE(revoked_reason, p_reason),
    token_enc = '',
    next_attempt_at = NULL
  WHERE id = p_installation_id AND org_id = p_org_id;
$$;

-- Cross-org sweep: returns ONLY ids of installations due for a run. No token, no content.
CREATE OR REPLACE FUNCTION public.list_due_judgeme_installations(p_interval_minutes integer)
RETURNS TABLE(installation_id uuid, org_id uuid)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
STABLE
AS $$
  SELECT ji.id, ji.org_id
  FROM public.judgeme_installations ji
  WHERE ji.revoked_at IS NULL
    AND ji.consecutive_failures < 10
    AND (ji.next_attempt_at IS NULL OR ji.next_attempt_at <= now())
    AND (ji.last_sync_at IS NULL
         OR ji.last_sync_at <= now() - make_interval(mins => p_interval_minutes))
  ORDER BY ji.last_sync_at NULLS FIRST;
$$;

ALTER FUNCTION public.upsert_judgeme_installation OWNER TO review_iq_migrator;
ALTER FUNCTION public.record_judgeme_sync OWNER TO review_iq_migrator;
ALTER FUNCTION public.revoke_judgeme_installation OWNER TO review_iq_migrator;
ALTER FUNCTION public.list_due_judgeme_installations OWNER TO review_iq_migrator;
REVOKE ALL ON FUNCTION public.upsert_judgeme_installation FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.record_judgeme_sync FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.revoke_judgeme_installation FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.list_due_judgeme_installations FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.upsert_judgeme_installation TO review_iq_app;
GRANT EXECUTE ON FUNCTION public.record_judgeme_sync TO review_iq_app;
GRANT EXECUTE ON FUNCTION public.revoke_judgeme_installation TO review_iq_app;
GRANT EXECUTE ON FUNCTION public.list_due_judgeme_installations TO review_iq_app;

-- ---------------------------------------------------------------------------
-- Postconditions -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py.
-- ---------------------------------------------------------------------------
-- @postcondition: judgeme_tables_columns_and_constraints
-- SQL: SELECT (SELECT count(*) FROM pg_attribute a WHERE a.attrelid =
-- SQL: 'public.judgeme_installations'::regclass AND a.attnum > 0 AND NOT a.attisdropped AND
-- SQL: (a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull) IN (('id', 'uuid', true),
-- SQL: ('org_id', 'uuid', true), ('shop_domain', 'text', true), ('token_enc', 'text', true),
-- SQL: ('auth_transport', 'text', true), ('installed_at', 'timestamp with time zone', true),
-- SQL: ('revoked_at', 'timestamp with time zone', false), ('revoked_reason', 'text', false),
-- SQL: ('last_sync_at', 'timestamp with time zone', false), ('last_sync_status', 'text', false),
-- SQL: ('last_sync_error', 'text', false), ('last_sync_summary', 'jsonb', false),
-- SQL: ('watermark', 'timestamp with time zone', false), ('last_full_scan_at', 'timestamp with
-- SQL: time zone', false), ('order_violation_at', 'timestamp with time zone', false),
-- SQL: ('consecutive_failures', 'integer', true), ('next_attempt_at', 'timestamp with time
-- SQL: zone', false))) = 17 AND (SELECT count(*) FROM pg_attribute a WHERE a.attrelid =
-- SQL: 'public.judgeme_review_state'::regclass AND a.attnum > 0 AND NOT a.attisdropped AND
-- SQL: (a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull) IN
-- SQL: (('installation_id', 'uuid', true), ('review_id', 'text', true), ('org_id', 'uuid', true),
-- SQL: ('content_hash', 'text', true), ('input_hash', 'text', true), ('state', 'text', true),
-- SQL: ('missing_strikes', 'smallint', true), ('source_created_at', 'timestamp with time zone',
-- SQL: false), ('source_updated_at', 'timestamp with time zone', false), ('updated_at',
-- SQL: 'timestamp with time zone', true))) = 10 AND EXISTS (SELECT 1 FROM pg_constraint c WHERE
-- SQL: c.conrelid = 'public.judgeme_installations'::regclass AND c.contype = 'u' AND
-- SQL: pg_get_constraintdef(c.oid) = 'UNIQUE (shop_domain)') AND EXISTS (SELECT 1 FROM
-- SQL: pg_constraint c WHERE c.conrelid = 'public.judgeme_installations'::regclass AND c.conname =
-- SQL: 'judgeme_inst_active_has_token' AND c.contype = 'c') AND EXISTS (SELECT 1 FROM
-- SQL: pg_constraint c WHERE c.conrelid = 'public.judgeme_review_state'::regclass AND c.contype =
-- SQL: 'p' AND pg_get_constraintdef(c.oid) = 'PRIMARY KEY (installation_id, review_id)') AND
-- SQL: EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conrelid =
-- SQL: 'public.judgeme_installations'::regclass AND c.contype = 'f' AND c.confrelid =
-- SQL: 'public.organizations'::regclass AND c.confdeltype = 'c')

-- @postcondition: judgeme_rls_and_policies
-- SQL: SELECT (SELECT count(*) = 2 AND bool_and(c.relrowsecurity) FROM pg_class c WHERE c.oid IN
-- SQL: ('public.judgeme_installations'::regclass, 'public.judgeme_review_state'::regclass)) AND
-- SQL: (SELECT count(*) FROM pg_policies p WHERE p.schemaname = 'public' AND (p.tablename,
-- SQL: p.policyname, p.cmd, p.roles::text) IN (('judgeme_installations',
-- SQL: 'judgeme_inst_authenticated_select', 'SELECT', '{authenticated}'), ('judgeme_installations',
-- SQL: 'judgeme_inst_anon_deny', 'ALL', '{anon}'), ('judgeme_review_state',
-- SQL: 'judgeme_state_authenticated_all', 'ALL', '{authenticated}'), ('judgeme_review_state',
-- SQL: 'judgeme_state_anon_deny', 'ALL', '{anon}'))) = 4 AND (SELECT count(*) FROM pg_policies p
-- SQL: WHERE p.schemaname = 'public' AND p.policyname IN ('judgeme_inst_authenticated_select',
-- SQL: 'judgeme_state_authenticated_all') AND p.qual LIKE '%current_org_id()%') = 2

-- @postcondition: judgeme_grants_narrowed
-- SQL: SELECT has_table_privilege('authenticated', 'public.judgeme_installations', 'SELECT') AND
-- SQL: NOT has_table_privilege('authenticated', 'public.judgeme_installations',
-- SQL: 'INSERT,UPDATE,DELETE') AND NOT has_table_privilege('anon', 'public.judgeme_installations',
-- SQL: 'SELECT,INSERT,UPDATE,DELETE') AND NOT has_table_privilege('anon',
-- SQL: 'public.judgeme_review_state', 'SELECT,INSERT,UPDATE,DELETE') AND
-- SQL: has_table_privilege('authenticated', 'public.judgeme_review_state', 'SELECT') AND
-- SQL: has_table_privilege('authenticated', 'public.judgeme_review_state', 'INSERT') AND
-- SQL: has_table_privilege('authenticated', 'public.judgeme_review_state', 'DELETE')

-- @postcondition: judgeme_functions_hardened
-- SQL: SELECT count(*) = 4 AND bool_and(p.prosecdef AND pg_get_userbyid(p.proowner) =
-- SQL: 'review_iq_migrator' AND p.proconfig @> ARRAY['search_path=public'] AND
-- SQL: has_function_privilege('review_iq_app', p.oid, 'EXECUTE') AND NOT
-- SQL: has_function_privilege('anon', p.oid, 'EXECUTE') AND NOT
-- SQL: has_function_privilege('authenticated', p.oid, 'EXECUTE') AND NOT
-- SQL: has_function_privilege('service_role', p.oid, 'EXECUTE')) FROM pg_proc p WHERE p.oid IN
-- SQL: (to_regprocedure('public.upsert_judgeme_installation(uuid,text,text,text)'),
-- SQL: to_regprocedure('public.record_judgeme_sync(uuid,uuid,text,text,timestamptz,timestamptz,boolean,jsonb,timestamptz,boolean)'),
-- SQL: to_regprocedure('public.revoke_judgeme_installation(uuid,uuid,text)'),
-- SQL: to_regprocedure('public.list_due_judgeme_installations(integer)'))
