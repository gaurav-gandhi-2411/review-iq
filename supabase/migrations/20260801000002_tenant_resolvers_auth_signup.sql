-- Migration: narrow SECURITY DEFINER resolvers for the auth/signup query sites that were
-- found to still depend on review_iq_app holding BYPASSRLS, after the webhook paths were
-- already fixed by 20260801000001's statement 3 (see PR #61). This migration is additive
-- only -- it does NOT touch review_iq_app's BYPASSRLS attribute (that ALTER ROLE already
-- lives in 20260801000001's statement 4; do not duplicate or re-run it here). Per the
-- current orchestration decision, the cutover is not being re-attempted until the code
-- that depends on these functions is deployed and its own test suite is green -- see
-- ops/runbooks/bypassrls-remediation-cutover.md.
--
-- Audit (2026-08-01, CI guard added in the same pass -- scripts/check_undocumented_pg_connects.py)
-- found 4 remaining call sites where org_id is unknown until a query resolves it, with no
-- resolver and no _set_tenant():
--   - app/auth/api_key.py::_lookup_and_record        (key_prefix -> org_id)
--   - app/auth/session.py::_lookup_and_record_for_session / _lookup_context_for_read
--                                                     (user_id -> org_id)
--   - app/auth/signup.py::_get_org_for_user           (user_id -> org_id, same shape)
--   - app/auth/signup.py::_provision_org_and_key       (org doesn't exist yet -- special case)
--
-- Same design as the webhook fix: narrow functions return ONLY the minimum needed to
-- proceed (org_id, or the freshly-created row's own id/timestamp), never a full row,
-- never key_hash/token material beyond what the immediate caller already legitimately
-- handles in-process.

-- ---------------------------------------------------------------------------
-- 1. resolve_org_for_user -- user_id -> org_id. Used by both
--    app/auth/signup.py::_get_org_for_user and app/auth/session.py::_lookup_context_for_read
--    (both are read-only "does this user have an org yet" checks).
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.resolve_org_for_user(p_user_id uuid)
RETURNS uuid
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
STABLE
AS $$
  SELECT org_id
  FROM public.organization_members
  WHERE user_id = p_user_id
  LIMIT 1;
$$;

COMMENT ON FUNCTION public.resolve_org_for_user IS
  'BYPASSRLS remediation (2c): narrow SECURITY DEFINER lookup replacing a bypass-holding '
  'role for user_id -> org_id resolution (signup.py, session.py read path). Returns ONLY '
  'org_id (or NULL) -- never api_keys/organizations columns. organization_members_user_id_key '
  '(20260801000001 statement 6) guarantees at most one row.';

ALTER FUNCTION public.resolve_org_for_user OWNER TO review_iq_migrator;
REVOKE ALL ON FUNCTION public.resolve_org_for_user FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.resolve_org_for_user TO review_iq_app;

-- ---------------------------------------------------------------------------
-- 2. resolve_org_for_api_key_prefix -- key_prefix -> org_id. Used by
--    app/auth/api_key.py::_lookup_and_record and app/auth/session.py's write path (the
--    FOR UPDATE row lock itself is taken in a SECOND, _set_tenant()-scoped query after
--    org_id is known -- see those files -- so this function never holds a lock and never
--    returns key_hash).
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.resolve_org_for_api_key_prefix(p_key_prefix text)
RETURNS uuid
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
STABLE
AS $$
  SELECT org_id
  FROM public.api_keys
  WHERE key_prefix = p_key_prefix AND revoked_at IS NULL
  LIMIT 1;
$$;

COMMENT ON FUNCTION public.resolve_org_for_api_key_prefix IS
  'BYPASSRLS remediation (2c): narrow SECURITY DEFINER lookup replacing a bypass-holding '
  'role for key_prefix -> org_id resolution (api_key.py, session.py write path). Returns '
  'ONLY org_id (or NULL) -- never key_hash, never quota. api_keys_key_prefix_key '
  '(20260801000001 statement 5) guarantees at most one row. Callers MUST re-fetch the row '
  'they need (id, key_hash, quota, ...) via a second, _set_tenant()-scoped, org_id-bound '
  'query -- this function does not (and must not) return that data.';

ALTER FUNCTION public.resolve_org_for_api_key_prefix OWNER TO review_iq_migrator;
REVOKE ALL ON FUNCTION public.resolve_org_for_api_key_prefix FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.resolve_org_for_api_key_prefix TO review_iq_app;

-- ---------------------------------------------------------------------------
-- 3. create_org_and_membership -- app/auth/signup.py::_provision_org_and_key's org-creation
--    half. There is no existing org to resolve here (this IS the creation), so the shape
--    differs from the two lookups above: this function performs the writes itself, under
--    the function owner's privileges, rather than returning data for the caller to write
--    with. Raises the same organization_members_user_id_key UniqueViolation on a
--    concurrent-signup race that app/auth/signup.py's _ProvisionRaceLost handling already
--    expects and catches -- unchanged behavior, just no longer requiring review_iq_app
--    itself to hold table-level INSERT privileges (let alone BYPASSRLS) on organizations /
--    organization_members.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.create_org_and_membership(p_user_id uuid, p_name text, p_slug text)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_org_id uuid;
BEGIN
  INSERT INTO public.organizations (name, slug) VALUES (p_name, p_slug) RETURNING id INTO v_org_id;
  INSERT INTO public.organization_members (org_id, user_id, role) VALUES (v_org_id, p_user_id, 'owner');
  RETURN v_org_id;
END;
$$;

COMMENT ON FUNCTION public.create_org_and_membership IS
  'BYPASSRLS remediation (2c): first-login org provisioning (app/auth/signup.py::'
  '_provision_org_and_key). SECURITY DEFINER so review_iq_app needs no direct INSERT grant '
  'on organizations/organization_members at all. Raises UniqueViolation on '
  'organization_members_user_id_key when a concurrent /auth/provision call for the same '
  'user_id already won -- caller (signup.py) already catches this as _ProvisionRaceLost and '
  'returns the winner''s org instead of a 500; unchanged by this migration.';

ALTER FUNCTION public.create_org_and_membership OWNER TO review_iq_migrator;
REVOKE ALL ON FUNCTION public.create_org_and_membership FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.create_org_and_membership TO review_iq_app;

-- ---------------------------------------------------------------------------
-- 4. create_api_key_for_org -- app/auth/signup.py::_provision_org_and_key's key-insertion
--    half, called from inside app/auth/keygen.py::insert_api_key_with_retry's existing
--    SAVEPOINT retry loop (unchanged retry semantics -- a key_prefix collision here still
--    raises UniqueViolation, still gets caught and retried with a freshly generated key by
--    the same Python loop; this function does not know about retries at all).
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.create_api_key_for_org(
  p_org_id uuid, p_key_hash text, p_key_prefix text, p_name text, p_quota int
)
RETURNS TABLE(id uuid, created_at timestamptz)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  INSERT INTO public.api_keys (org_id, key_hash, key_prefix, name, quota)
  VALUES (p_org_id, p_key_hash, p_key_prefix, p_name, p_quota)
  RETURNING id, created_at;
$$;

COMMENT ON FUNCTION public.create_api_key_for_org IS
  'BYPASSRLS remediation (2c): api_keys row creation for first-login provisioning '
  '(app/auth/signup.py::_provision_org_and_key, via app/auth/keygen.py::'
  'insert_api_key_with_retry). SECURITY DEFINER so review_iq_app needs no direct INSERT '
  'grant on api_keys for this path. Raises UniqueViolation on api_keys_key_prefix_key on a '
  'collision -- caller''s existing SAVEPOINT retry loop is unchanged by this migration.';

ALTER FUNCTION public.create_api_key_for_org OWNER TO review_iq_migrator;
REVOKE ALL ON FUNCTION public.create_api_key_for_org FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.create_api_key_for_org TO review_iq_app;

-- ---------------------------------------------------------------------------
-- 5/6. upsert_google_installation / upsert_shopify_installation --
--    app/api/{google,shopify}_auth.py::_upsert_installation_pg. Found live 2026-08-01
--    while building the pre-cutover ephemeral-Postgres CI job (P3): a _set_tenant()-only
--    fix (an earlier, incomplete pass of this same remediation) is NOT sufficient here,
--    unlike every other write in this migration. `authenticated` holds only a SELECT
--    policy on google_business_installations/shopify_installations (see
--    20260702000001_google_business_installations.sql / 20260622000001_shopify_
--    installations.sql -- both name their policy "..._authenticated_select", not
--    "_all") -- there has never been an INSERT/UPDATE policy for authenticated on
--    either table, because the only prior writer was review_iq_app's own BYPASSRLS
--    grant. Switching the connecting role to `authenticated` via _set_tenant() for an
--    INSERT ... ON CONFLICT DO UPDATE therefore fails outright once BYPASSRLS is
--    removed -- proven live: `new row violates row-level security policy for table
--    "google_business_installations"` against a real ephemeral database with the
--    NOBYPASSRLS migration applied. Fix: narrow SECURITY DEFINER upsert functions,
--    same pattern as create_org_and_membership/create_api_key_for_org above, rather
--    than widening the RLS policy surface on these two tables.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.upsert_google_installation(
  p_org_id uuid, p_account_name text, p_location_name text, p_refresh_token_enc text
)
RETURNS void
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  INSERT INTO public.google_business_installations
    (org_id, google_account_name, google_location_name, refresh_token_enc)
  VALUES (p_org_id, p_account_name, p_location_name, p_refresh_token_enc)
  ON CONFLICT (google_location_name)
  DO UPDATE SET
    refresh_token_enc = EXCLUDED.refresh_token_enc,
    revoked_at        = NULL,
    installed_at      = now();
$$;

COMMENT ON FUNCTION public.upsert_google_installation IS
  'BYPASSRLS remediation (2c, corrected): review_iq_app has no direct INSERT/UPDATE '
  'grant on google_business_installations -- authenticated only holds a SELECT policy '
  '(see 20260702000001_google_business_installations.sql). org_id is ALWAYS '
  'caller-resolved from the verified JWT before this is called -- never a user param.';

ALTER FUNCTION public.upsert_google_installation OWNER TO review_iq_migrator;
REVOKE ALL ON FUNCTION public.upsert_google_installation FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.upsert_google_installation TO review_iq_app;

CREATE OR REPLACE FUNCTION public.upsert_shopify_installation(
  p_org_id uuid, p_shop_domain text, p_access_token_enc text
)
RETURNS void
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  INSERT INTO public.shopify_installations (org_id, shop_domain, access_token_enc)
  VALUES (p_org_id, p_shop_domain, p_access_token_enc)
  ON CONFLICT (shop_domain)
  DO UPDATE SET
    access_token_enc = EXCLUDED.access_token_enc,
    revoked_at       = NULL,
    installed_at     = now();
$$;

COMMENT ON FUNCTION public.upsert_shopify_installation IS
  'BYPASSRLS remediation (2c, corrected): review_iq_app has no direct INSERT/UPDATE '
  'grant on shopify_installations -- authenticated only holds a SELECT policy (see '
  '20260622000001_shopify_installations.sql). org_id is ALWAYS caller-resolved from '
  'the verified JWT before this is called -- never a user param.';

ALTER FUNCTION public.upsert_shopify_installation OWNER TO review_iq_migrator;
REVOKE ALL ON FUNCTION public.upsert_shopify_installation FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.upsert_shopify_installation TO review_iq_app;

-- ---------------------------------------------------------------------------
-- 7/8. claim_pending_batch_job_row / settle_batch_job_row --
--    app/core/ingest_worker.py::_claim_one_row(). Found live 2026-08-01 in the same
--    ephemeral-Postgres CI-job pass as functions 5/6 above, and structurally identical
--    in cause: this claim query's own ALLOWLIST entry in scripts/check_undocumented_
--    pg_connects.py already documented that it "must see all orgs" without
--    _set_tenant() -- what it never addressed is HOW, once review_iq_app no longer
--    holds BYPASSRLS. Proven live: a real pending row, confirmed visible via the
--    postgres superuser, returns ZERO rows from the exact claim SQL run as
--    review_iq_app with no SET ROLE against an ephemeral database with NOBYPASSRLS
--    applied -- review_iq_app inherits `authenticated`'s policy automatically (it is
--    a member, see 20260726000001_review_iq_app_role.sql), but that policy is
--    `USING (org_id = current_org_id())`, and current_org_id() is NULL with no GUC
--    set -- so every row's comparison is UNKNOWN and RLS filters everything out. This
--    is not a missing-grant bug like functions 5/6 -- it is a structural gap: no
--    combination of _set_tenant()/SET ROLE can express "every org" under this
--    per-org RLS design. Left unfixed, this would have been a SILENT, TOTAL,
--    cross-org failure of the entire bulk-CSV-ingest queue the moment BYPASSRLS was
--    removed -- every drain_rows() call (both the submitting endpoint's own
--    BackgroundTask and the scheduled /internal/ingest/tick) would claim nothing,
--    forever, with no error raised anywhere.
--
--    Split into two functions (not one) because the caller holds the row's FOR
--    UPDATE lock OPEN across an awaited LLM extraction call (a deliberate,
--    documented design choice in _claim_one_row's own docstring -- this is what
--    makes concurrent drain_rows() callers structurally unable to double-process a
--    row) -- the claim and the settle are two separate statements in the SAME
--    outer transaction/connection, not one atomic function call. A SECURITY
--    DEFINER function's row lock is held into the CALLING transaction exactly like
--    any other statement, so this holds correctly across the two calls as long as
--    both run on the same connection without an intervening commit -- which
--    _claim_one_row already guarantees (one connection per claim, one commit at
--    the very end).
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.claim_pending_batch_job_row()
RETURNS TABLE(job_id text, row_index integer, org_id uuid, row_text text, product text, review_date timestamptz)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT job_id, row_index, org_id, text, product, review_date
  FROM public.batch_job_rows
  WHERE status = 'pending'
  ORDER BY updated_at
  LIMIT 1
  FOR UPDATE SKIP LOCKED;
$$;

COMMENT ON FUNCTION public.claim_pending_batch_job_row IS
  'BYPASSRLS remediation (2c): the durable bulk-ingest queue''s cross-org claim '
  '(app/core/ingest_worker.py::_claim_one_row). Must see pending rows across ALL '
  'orgs, which no per-org RLS policy expression can do -- SECURITY DEFINER bypasses '
  'RLS for exactly this one claim query. Row lock (FOR UPDATE SKIP LOCKED) is held '
  'into the calling transaction; caller MUST NOT commit until '
  'settle_batch_job_row() has also run on the same connection.';

ALTER FUNCTION public.claim_pending_batch_job_row OWNER TO review_iq_migrator;
REVOKE ALL ON FUNCTION public.claim_pending_batch_job_row FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.claim_pending_batch_job_row TO review_iq_app;

CREATE OR REPLACE FUNCTION public.settle_batch_job_row(
  p_job_id text, p_row_index integer, p_status text, p_error text, p_input_hash text
)
RETURNS void
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  UPDATE public.batch_job_rows
  SET status = p_status, error = p_error, input_hash = p_input_hash, updated_at = now()
  WHERE job_id = p_job_id AND row_index = p_row_index;
$$;

COMMENT ON FUNCTION public.settle_batch_job_row IS
  'BYPASSRLS remediation (2c): the settle half of claim_pending_batch_job_row() -- '
  'must run on the SAME connection/transaction as the claim, before that '
  'transaction commits, so the row lock from the claim is still held (preventing a '
  'concurrent drain_rows() caller from claiming the same row before this settle '
  'commits).';

ALTER FUNCTION public.settle_batch_job_row OWNER TO review_iq_migrator;
REVOKE ALL ON FUNCTION public.settle_batch_job_row FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.settle_batch_job_row TO review_iq_app;
