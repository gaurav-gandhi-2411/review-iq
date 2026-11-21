-- Migration: per-user last_seen_at on organization_members, behind two narrow SECURITY
-- DEFINER functions (S18 D3, ADR 0036).
--
-- Why: the product needs a per-USER "last seen" timestamp (what changed since I last looked).
-- organization_members has exactly one row per user (organization_members_user_id_key, UNIQUE
-- (user_id), 20260801000001 statement 6), so the column lives there rather than in a new table.
--
-- Access pattern: review_iq_app, authenticated, anon and service_role hold NO privilege on
-- organization_members (20260817000002), and this migration does not change that. The app
-- reads and writes the column ONLY through the two functions below, same shape as
-- resolve_org_for_user in 20260801000002: owner = review_iq_migrator, REVOKE ALL FROM PUBLIC /
-- anon / authenticated / service_role, EXECUTE to review_iq_app only. The functions are
-- user-scoped, not org-scoped: they take the user_id from the verified JWT (app side) and
-- touch only that user's single row. No org_id is read, returned or needed.
--
-- Debounce lives in touch_last_seen: the UPDATE only fires when the stored value is NULL or
-- older than 60 seconds; either way the function returns the value now stored. A burst of
-- page loads therefore costs one write per user per minute, enforced in the database
-- regardless of which client or code path calls it.
--
-- Additive / expand-only: nullable column, no default, NO backfill (a NULL means "never seen"
-- and is meaningful). Existing readers of organization_members are unchanged: every one of
-- them is a SECURITY DEFINER function selecting named columns (org_id) or inserting named
-- columns, none uses SELECT * or an unnamed column list.
--
-- Privacy: last_seen_at is per-user activity data (personal data). It is deleted with the
-- member row, which cascades from organizations (ON DELETE CASCADE, 20260510000001), i.e. it
-- goes with account deletion (DELETE /account). See docs/architecture/adr/0036-per-user-last-seen.md.
--
-- MIGRATION NOT APPLIED BY THE PR THAT ADDS THIS FILE: GG decides when it is applied.
-- Rollback (expand-only, nothing depends on it once the app code is reverted):
--   DROP FUNCTION public.touch_last_seen(uuid);
--   DROP FUNCTION public.get_last_seen(uuid);
--   ALTER TABLE public.organization_members DROP COLUMN last_seen_at;

ALTER TABLE public.organization_members ADD COLUMN IF NOT EXISTS last_seen_at timestamptz;

-- ---------------------------------------------------------------------------
-- 1. get_last_seen -- the user's stored last_seen_at (NULL = never seen, or no member row).
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.get_last_seen(p_user_id uuid)
RETURNS timestamptz
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
STABLE
AS $$
  SELECT last_seen_at
  FROM public.organization_members
  WHERE user_id = p_user_id
  LIMIT 1;
$$;

COMMENT ON FUNCTION public.get_last_seen IS
  'S18 D3: narrow SECURITY DEFINER read of organization_members.last_seen_at for ONE user. '
  'Returns ONLY the timestamp (NULL if never seen or the user has no member row) -- never '
  'org_id or role. organization_members_user_id_key guarantees at most one row.';

ALTER FUNCTION public.get_last_seen(uuid) OWNER TO review_iq_migrator;
REVOKE ALL ON FUNCTION public.get_last_seen(uuid) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.get_last_seen(uuid) TO review_iq_app;

-- ---------------------------------------------------------------------------
-- 2. touch_last_seen -- set last_seen_at = now() for the user's single member row, debounced
--    to one write per 60 seconds, and return the value now stored. Never raises: a user with
--    no member row gets NULL and nothing changes.
--
--    Concurrency: two simultaneous calls both target the same row; the second blocks on the
--    row lock, re-evaluates the WHERE clause under READ COMMITTED against the first call's
--    fresh value, matches nothing, and falls through to the SELECT. One write wins, both
--    callers get the same timestamp back.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.touch_last_seen(p_user_id uuid)
RETURNS timestamptz
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_seen timestamptz;
BEGIN
  UPDATE public.organization_members
  SET last_seen_at = now()
  WHERE user_id = p_user_id
    AND (last_seen_at IS NULL OR last_seen_at < now() - interval '60 seconds')
  RETURNING last_seen_at INTO v_seen;

  IF NOT FOUND THEN
    -- Debounced (value is under 60s old) or no member row: report what is stored (NULL if none).
    SELECT last_seen_at INTO v_seen
    FROM public.organization_members
    WHERE user_id = p_user_id;
  END IF;

  RETURN v_seen;
END;
$$;

COMMENT ON FUNCTION public.touch_last_seen IS
  'S18 D3: narrow SECURITY DEFINER write of organization_members.last_seen_at for ONE user. '
  'No-op returning the stored value if it is under 60 seconds old (server-side debounce); '
  'returns NULL and changes nothing if the user has no member row; never raises.';

ALTER FUNCTION public.touch_last_seen(uuid) OWNER TO review_iq_migrator;
REVOKE ALL ON FUNCTION public.touch_last_seen(uuid) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.touch_last_seen(uuid) TO review_iq_app;

-- ---------------------------------------------------------------------------
-- Postconditions -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py.
-- ---------------------------------------------------------------------------
-- @postcondition: organization_members_last_seen_column
-- SQL: SELECT count(*) = 1 AND bool_and(a.atttypid = 'timestamptz'::regtype AND NOT a.attnotnull
-- SQL: AND NOT a.atthasdef) FROM pg_attribute a WHERE a.attrelid =
-- SQL: 'public.organization_members'::regclass AND a.attname = 'last_seen_at' AND a.attnum > 0
-- SQL: AND NOT a.attisdropped

-- @postcondition: last_seen_functions_hardened
-- SQL: SELECT count(*) = 2 AND bool_and(p.prosecdef AND pg_get_userbyid(p.proowner) =
-- SQL: 'review_iq_migrator' AND p.prorettype = 'timestamptz'::regtype AND
-- SQL: array_to_string(p.proconfig, ',') LIKE 'search_path=public%pg_temp%' AND
-- SQL: has_function_privilege('review_iq_app', p.oid, 'EXECUTE') AND NOT
-- SQL: has_function_privilege('anon', p.oid, 'EXECUTE') AND NOT
-- SQL: has_function_privilege('authenticated', p.oid, 'EXECUTE') AND NOT
-- SQL: has_function_privilege('service_role', p.oid, 'EXECUTE')) FROM pg_proc p WHERE p.oid IN
-- SQL: (to_regprocedure('public.get_last_seen(uuid)'),
-- SQL: to_regprocedure('public.touch_last_seen(uuid)'))

-- Table grants stay closed to anon/authenticated (the state 20260817000002 established). Only those
-- two roles are asserted: service_role's table grant on this table is NOT closed by any earlier
-- migration (pre-existing, out of scope here), so asserting it would be a false postcondition.
-- @postcondition: organization_members_still_closed_to_anon_and_authenticated
-- SQL: SELECT count(*) = 2 AND bool_and(NOT has_table_privilege(r, 'public.organization_members',
-- SQL: 'SELECT') AND NOT has_table_privilege(r, 'public.organization_members', 'UPDATE')) FROM
-- SQL: unnest(ARRAY['anon', 'authenticated']) AS r
