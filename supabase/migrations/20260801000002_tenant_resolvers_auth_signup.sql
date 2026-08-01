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
