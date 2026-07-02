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
