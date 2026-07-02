-- Migration: google_business_installations table for multi-tenant Google Business Profile
-- OAuth installs.
--
-- PURPOSE:
--   Maps google_location_name (the `location_name` field on every Pub/Sub NEW_REVIEW
--   notification) → org_id so that webhook-driven review ingestion routes to the correct
--   org without any shared global API key. Mirrors shopify_installations exactly.
--
-- WHY refresh_token_enc, NOT access_token_enc (unlike shopify_installations):
--   Shopify's OAuth access_token never expires — it is stored and used directly.
--   Google's OAuth access_token expires in ~1 hour; the long-lived credential is the
--   refresh_token (obtained once, at install, with access_type=offline&prompt=consent).
--   The connector and webhook handler exchange refresh_token → short-lived access_token
--   on every API call. Only refresh_token is persisted.
--
-- WHY google_account_name (no Shopify analog):
--   GBP resource names are hierarchical: accounts/{account_id}/locations/{location_id}.
--   API calls that are account-scoped (e.g. re-listing locations) need the account
--   resource name; the location resource name alone is the routing key for notifications.
--
-- TOKEN ENCRYPTION:
--   refresh_token_enc stores each seller's Google OAuth refresh token encrypted with
--   Fernet (AES-128-CBC + HMAC-SHA256, from the Python cryptography package). The DB
--   never sees the plaintext token. Decryption requires GOOGLE_TOKEN_ENCRYPTION_KEY
--   from the app environment (Google Secret Manager in prod, .env.local for dev).
--
-- MULTI-TENANT SAFETY:
--   UNIQUE(google_location_name) is the anti-ambiguity gate — mirrors shop_domain in
--   shopify_installations. One Google Business location maps to exactly one org. A
--   re-install (new OAuth flow, e.g. after revocation) updates the existing row
--   (revoked_at → NULL, new refresh_token_enc) rather than creating a duplicate.
--
-- RLS MODEL (identical to shopify_installations):
--   authenticated role: SELECT only — sellers can see their own installation in a
--   future "Connected profiles" dashboard. INSERT/UPDATE/DELETE only via service-role
--   (postgres), used by the OAuth callback.
--
--   Service-role (postgres) bypasses RLS naturally — used by the webhook lookup path
--   (_get_google_installation_pg) and the OAuth callback.
--
-- ROLLBACK:
--   DROP TABLE public.google_business_installations;   -- CASCADE not needed; no FKs point here
--
-- Idempotent: CREATE TABLE/INDEX use IF NOT EXISTS; policy block drops before recreating.

CREATE TABLE IF NOT EXISTS public.google_business_installations (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id              UUID        NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    -- e.g. "accounts/123456789" — needed to scope account-level API calls.
    google_account_name TEXT        NOT NULL,
    -- e.g. "accounts/123456789/locations/987654321" — the routing key delivered on
    -- every NEW_REVIEW Pub/Sub notification. UNIQUE enforces one location → one org.
    google_location_name TEXT       NOT NULL UNIQUE,
    -- Fernet-encrypted Google OAuth refresh token (AES-128-CBC + HMAC-SHA256).
    -- Plaintext never stored. Requires GOOGLE_TOKEN_ENCRYPTION_KEY to decrypt.
    refresh_token_enc   TEXT        NOT NULL,
    installed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- NULL = active installation. Set to now() to revoke without deleting audit history.
    revoked_at          TIMESTAMPTZ
);

-- Hot path: webhook lookup by google_location_name (only active installs)
CREATE INDEX IF NOT EXISTS idx_gbp_inst_location_active
    ON public.google_business_installations (google_location_name)
    WHERE revoked_at IS NULL;

-- Org-scoped index for the dashboard "Connected profiles" query
CREATE INDEX IF NOT EXISTS idx_gbp_inst_org_id
    ON public.google_business_installations (org_id);

-- Supabase DEFAULT PRIVILEGES pre-grant all privileges to authenticated (authenticated=arwdDxtm)
-- regardless of explicit GRANT lines in migrations. The GRANT below is documentation-only.
-- INSERT block is enforced by RLS: no INSERT policy for authenticated → PostgreSQL default-deny.
-- Writes (INSERT on install, UPDATE on revoke) are service-role (postgres, no SET ROLE) only.
GRANT SELECT ON public.google_business_installations TO authenticated;

ALTER TABLE public.google_business_installations ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    DROP POLICY IF EXISTS "gbp_inst_authenticated_select" ON public.google_business_installations;
    DROP POLICY IF EXISTS "gbp_inst_anon_deny"            ON public.google_business_installations;
END $$;

-- Sellers see only their own installations (future dashboard use).
-- WITH CHECK omitted: no INSERT policy for authenticated means any INSERT is default-denied
-- by RLS before WITH CHECK would apply. Service-role INSERT bypasses RLS entirely.
CREATE POLICY "gbp_inst_authenticated_select" ON public.google_business_installations
    FOR SELECT TO authenticated
    USING (org_id = public.current_org_id());

-- Anon gets nothing.
CREATE POLICY "gbp_inst_anon_deny" ON public.google_business_installations
    FOR ALL TO anon USING (false);
