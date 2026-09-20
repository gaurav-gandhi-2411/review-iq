-- Migration: shopify_installations table for multi-tenant Shopify OAuth installs.
--
-- PURPOSE:
--   Maps shop_domain (X-Shopify-Shop-Domain webhook header) → org_id so that
--   webhook-driven review ingestion routes to the correct org without any shared
--   global API key.
--
-- TOKEN ENCRYPTION:
--   access_token_enc stores each seller's Shopify OAuth token encrypted with Fernet
--   (AES-128-CBC + HMAC-SHA256, from the Python cryptography package).
--   The DB never sees the plaintext token. Decryption requires SHOPIFY_TOKEN_ENCRYPTION_KEY
--   from the app environment (Google Secret Manager in prod, .env.local for dev).
--   Rationale: plaintext storage means a DB/backup leak exposes every connected store's
--   token immediately. Fernet provides authenticated encryption — a tampered ciphertext
--   raises InvalidToken before any decryption occurs.
--
-- MULTI-TENANT SAFETY:
--   UNIQUE(shop_domain) is the anti-ambiguity gate. One shop_domain can only map to
--   one org_id. If a seller re-installs (new OAuth flow), the existing row must be
--   updated (revoked_at → NULL, new access_token_enc) — a duplicate INSERT will fail
--   the UNIQUE constraint rather than creating a second ambiguous row.
--
-- RLS MODEL:
--   authenticated role: SELECT only — sellers can see their own installation in a
--   future "Connected stores" dashboard. INSERT/UPDATE/DELETE only via service-role
--   (postgres), used by the OAuth callback.
--
--   Service-role (postgres) bypasses RLS naturally — used by webhook lookup path
--   (_get_shopify_installation_pg) and the OAuth callback.
--
-- ROLLBACK:
--   DROP TABLE public.shopify_installations;   -- CASCADE not needed; no FKs point here
--
-- Idempotent: CREATE TABLE/INDEX use IF NOT EXISTS; policy block drops before recreating.

CREATE TABLE IF NOT EXISTS public.shopify_installations (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id           UUID        NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    -- UNIQUE enforces one shop → one org. Webhook routing is never ambiguous.
    shop_domain      TEXT        NOT NULL UNIQUE,
    -- Fernet-encrypted Shopify OAuth access token (AES-128-CBC + HMAC-SHA256).
    -- Plaintext never stored. Requires SHOPIFY_TOKEN_ENCRYPTION_KEY to decrypt.
    access_token_enc TEXT        NOT NULL,
    installed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- NULL = active installation. Set to now() to revoke without deleting audit history.
    revoked_at       TIMESTAMPTZ
);

-- Hot path: webhook lookup by shop_domain (only active installs)
CREATE INDEX IF NOT EXISTS idx_shopify_inst_shop_domain_active
    ON public.shopify_installations (shop_domain)
    WHERE revoked_at IS NULL;

-- Org-scoped index for the dashboard "Connected stores" query
CREATE INDEX IF NOT EXISTS idx_shopify_inst_org_id
    ON public.shopify_installations (org_id);

-- Supabase DEFAULT PRIVILEGES pre-grant all privileges to authenticated (authenticated=arwdDxtm)
-- regardless of explicit GRANT lines in migrations. The GRANT below is documentation-only.
-- INSERT block is enforced by RLS: no INSERT policy for authenticated → PostgreSQL default-deny.
-- Writes (INSERT on install, UPDATE on revoke) are service-role (postgres, no SET ROLE) only.
GRANT SELECT ON public.shopify_installations TO authenticated;

ALTER TABLE public.shopify_installations ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    DROP POLICY IF EXISTS "shopify_inst_authenticated_select" ON public.shopify_installations;
    DROP POLICY IF EXISTS "shopify_inst_anon_deny"            ON public.shopify_installations;
END $$;

-- Sellers see only their own installations (future dashboard use).
-- WITH CHECK omitted: no INSERT policy for authenticated means any INSERT is default-denied
-- by RLS before WITH CHECK would apply. Service-role INSERT bypasses RLS entirely.
CREATE POLICY "shopify_inst_authenticated_select" ON public.shopify_installations
    FOR SELECT TO authenticated
    USING (org_id = public.current_org_id());

-- Anon gets nothing.
CREATE POLICY "shopify_inst_anon_deny" ON public.shopify_installations
    FOR ALL TO anon USING (false);

-- ---------------------------------------------------------------------------
-- Postconditions (Session 15d) -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py. Each holds against the FINAL
-- schema state (a later migration must not undo it), in the CI build and in production.
-- ---------------------------------------------------------------------------
-- @postcondition: shopify_installations_columns_and_constraints
-- SQL: SELECT (SELECT count(*) FROM pg_attribute a WHERE a.attrelid =
-- SQL: 'public.shopify_installations'::regclass AND a.attnum > 0 AND NOT a.attisdropped AND
-- SQL: (a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull) IN (('id', 'uuid', true),
-- SQL: ('org_id', 'uuid', true), ('shop_domain', 'text', true), ('access_token_enc', 'text', true),
-- SQL: ('installed_at', 'timestamp with time zone', true), ('revoked_at',
-- SQL: 'timestamp with time zone', false))) = 6 AND EXISTS (SELECT 1 FROM pg_constraint c WHERE
-- SQL: c.conrelid = 'public.shopify_installations'::regclass AND c.contype = 'u' AND
-- SQL: pg_get_constraintdef(c.oid) = 'UNIQUE (shop_domain)') AND EXISTS (SELECT 1 FROM
-- SQL: pg_constraint c WHERE c.conrelid = 'public.shopify_installations'::regclass AND c.contype =
-- SQL: 'f' AND c.confrelid = 'public.organizations'::regclass AND c.confdeltype = 'c')

-- @postcondition: shopify_installations_indexes
-- SQL: SELECT EXISTS (SELECT 1 FROM pg_index i WHERE i.indexrelid =
-- SQL: to_regclass('public.idx_shopify_inst_shop_domain_active') AND i.indrelid =
-- SQL: 'public.shopify_installations'::regclass AND i.indpred IS NOT NULL) AND EXISTS (SELECT 1
-- SQL: FROM pg_index i WHERE i.indexrelid = to_regclass('public.idx_shopify_inst_org_id') AND
-- SQL: i.indrelid = 'public.shopify_installations'::regclass)

-- @postcondition: shopify_installations_rls_and_policies
-- SQL: SELECT (SELECT count(*) = 1 AND bool_and(c.relrowsecurity) FROM pg_class c WHERE c.oid IN
-- SQL: ('public.shopify_installations'::regclass)) AND (SELECT count(*) FROM pg_policies p WHERE
-- SQL: p.schemaname = 'public' AND (p.tablename, p.policyname, p.cmd, p.roles::text) IN
-- SQL: (('shopify_installations', 'shopify_inst_authenticated_select', 'SELECT',
-- SQL: '{authenticated}'), ('shopify_installations', 'shopify_inst_anon_deny', 'ALL', '{anon}'))) =
-- SQL: 2 AND (SELECT count(*) FROM pg_policies p WHERE p.schemaname = 'public' AND p.policyname IN
-- SQL: ('shopify_inst_authenticated_select') AND p.qual LIKE '%current_org_id()%') = 1 AND (SELECT
-- SQL: count(*) FROM pg_policies p WHERE p.schemaname = 'public' AND p.policyname IN
-- SQL: ('shopify_inst_anon_deny') AND p.qual = 'false') = 1
