# ADR 0036: per-user last_seen_at on organization_members, behind two SECURITY DEFINER functions

Status: proposed (S18 D3, backend half). The migration is written and reviewed but NOT applied;
GG decides when. The frontend that calls the endpoints is a separate change.

## Context

The product needs a per-USER "last seen" timestamp so the dashboard can show what changed since
the signed-in user last looked. The existing session context (`ApiKeyContext`) is per-org, so
this cannot live on an org-level row without conflating the members of one org.

`public.organization_members` has one row per user (`organization_members_user_id_key`, UNIQUE
(user_id), `20260801000001` statement 6). `anon` and `authenticated` hold no privilege on it
(`20260817000002`), and `review_iq_app` reaches it only through narrow SECURITY DEFINER
functions (`resolve_org_for_user`, `create_org_and_membership` in `20260801000002`).

## Decision

1. **Column**: `ALTER TABLE public.organization_members ADD COLUMN IF NOT EXISTS last_seen_at
   timestamptz` -- nullable, no default, no backfill. NULL means "never seen" and is meaningful.
   Expand-only; nothing existing changes.
2. **Access**: two functions, same shape as `resolve_org_for_user`: owner `review_iq_migrator`,
   SECURITY DEFINER, `SET search_path = public, pg_temp`, `REVOKE ALL ... FROM PUBLIC, anon,
   authenticated, service_role`, `EXECUTE` to `review_iq_app` only. The table stays closed.
   - `public.get_last_seen(p_user_id uuid) RETURNS timestamptz`
   - `public.touch_last_seen(p_user_id uuid) RETURNS timestamptz` -- sets `now()` on that user's
     row and returns the stored value; returns NULL and changes nothing for a user with no
     member row; never raises.
3. **Debounce in SQL, not in Python**: `touch_last_seen` updates only `WHERE last_seen_at IS NULL
   OR last_seen_at < now() - interval '60 seconds'` and otherwise returns the stored value. The
   write budget (one write per user per minute) is then enforced regardless of which client or
   code path calls it, survives multiple Cloud Run instances (an in-process limiter would not),
   and needs no new store. Concurrent calls serialize on the row lock; the second re-evaluates
   the predicate under READ COMMITTED and falls through to the read. Cost: the debounce window
   is a constant in the function body; changing it is a new migration (`CREATE OR REPLACE`).
4. **Endpoints** (BFF, `app/api/bff/router.py`): `GET /bff/account/last-seen` and
   `POST /bff/account/last-seen`, both returning `{"last_seen_at": <ISO 8601 | null>}`. The user
   id comes ONLY from the verified Supabase JWT (`require_verified_user_id`, which does no org
   lookup, quota check or usage record); no user id is accepted in the path, query or body.
   POST for a user with no account yet returns 404 (GET returns null).
5. **Storage**: `get_last_seen_pg` / `touch_last_seen_pg` in `app/core/storage_pg.py`. They are
   user-scoped, so there is no org to `_set_tenant()` to; both are reasoned entries in
   `scripts/check_undocumented_pg_connects.py` ALLOWLIST, like `insert_lead_pg`.
6. **Controls**: three postconditions in the migration (column shape; both functions hardened;
   table still closed to anon/authenticated), an UNDO_CASES entry for each in
   `tests/integration/test_push_postconditions.py`, and a behavioural integration test
   `tests/integration/test_last_seen_functions.py`.

## Consequences

- **Blast radius**: new column and two new functions only. Every existing consumer of
  `organization_members` is a SECURITY DEFINER function naming its columns (`org_id`,
  `user_id`, `role`), none uses `SELECT *`, so none sees the new column. No existing grant,
  policy, or endpoint changes. Until the migration is applied the new endpoints would fail at
  the database (function does not exist) and return 500; nothing else depends on them, and the
  frontend half must not ship before the migration is applied.
- **Write load**: at most one UPDATE per active user per minute on a row that is already hot
  only at sign-in. A row update rewrites the tuple (the table is tiny; one row per user).
- **Not covered**: `service_role` still holds Supabase's default table grant on
  `organization_members` (pre-existing, not introduced here; not asserted by this migration's
  postconditions, which cover `anon` and `authenticated` only).
- **Privacy**: `last_seen_at` is a per-user activity timestamp, i.e. personal data. It lives on
  the member row, so it is deleted with the org via the existing `ON DELETE CASCADE` chain
  (`DELETE /account`); no new deletion path is needed. It is not exported or logged by this
  change. **GG must approve a line in `legal/privacy-policy.md` and
  `legal/data-retention-and-deletion.md` (and the retention schedule) describing it before the
  frontend uses it for real users; this change does not edit `legal/`.** Open question for GG:
  retention while an account stays open is currently "until account deletion"; confirm that is
  the intended schedule.
- **Rollback**: `DROP FUNCTION public.touch_last_seen(uuid); DROP FUNCTION
  public.get_last_seen(uuid); ALTER TABLE public.organization_members DROP COLUMN last_seen_at;`
  (also in the migration header). Revert the app code first.

## Alternatives

- **New `user_activity` table**: cleaner separation, but a second per-user table with its own
  RLS, grants and cascade for one timestamp; the one-row-per-user member row already exists and
  cascades. Rejected as more surface for the same data.
- **Grant `review_iq_app` UPDATE (last_seen_at) on the table**: reopens a table deliberately
  closed in `20260817000002`, and the column grant would let the app role touch any user's row
  with no per-user binding. Rejected; the function takes the user id as its only handle.
- **Debounce in the BFF (in-process or Redis)**: per-instance state on Cloud Run is unreliable
  and Redis is a new dependency. Rejected for the SQL predicate.
- **Debounce in the client only**: not a control; any client can call the endpoint on every
  request. Kept as a cheap complement (the frontend should still not call on every render).
- **Store in the Supabase auth user metadata**: couples the product to a vendor admin API call
  and the service-role key, which has just been removed as a consumer (S17 W1). Rejected.
