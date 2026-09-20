-- Migration: close issue #186 -- public._migrations has RLS disabled and anon holds a grant.
--
-- public._migrations is tooling bookkeeping created by supabase/push.py's own migration
-- runner (CREATE TABLE IF NOT EXISTS, no explicit GRANT/REVOKE), not app schema managed by
-- a migration file -- which is why schema-drift-check.yml (scoped to migration-file-managed
-- objects) never caught this, while the separate, PR-triggered "ACL Exposure Check" did.
-- Postgres's default PUBLIC-inherited grants mean anon and authenticated both hold SELECT on
-- it the same way current_org_id() held an unintended EXECUTE grant (see
-- 20260912000003_close_pre_existing_grant_drift.sql for the identical root cause on a
-- different object type).
--
-- Content is two columns: filename (migration file names, e.g.
-- "20260622000001_shopify_installations.sql") and applied_at (a timestamp). No customer or
-- end-customer data. Severity: LOW, same tier as the current_org_id finding -- not a
-- cross-tenant or customer-data exposure, but real reconnaissance value (migration filenames
-- narrate this project's schema/feature-rollout history) and a genuine least-privilege
-- violation (rule 96) with zero legitimate anon/authenticated use case: the only code that
-- ever reads or writes this table is supabase/push.py, running as the migration owner role
-- directly against SUPABASE_DIRECT_URL, never through the app's anon/authenticated
-- Postgres roles.
--
-- REVOKE ALL rather than RLS + a deny policy: RLS is the right tool when some rows should be
-- visible and others not (per-tenant isolation); here no row should ever be visible to these
-- roles, so an outright REVOKE is both simpler and stronger (rule 74's tie-breaker: simplest
-- solution that satisfies the constraint).
--
-- Guarded on the table existing: public._migrations is created by supabase/push.py, NOT by any
-- migration file, so the pre-cutover-verification job (which replays every migration file into
-- a fresh Postgres with plain psql, never running push.py) has no such table and an unguarded
-- REVOKE fails it with `relation "public._migrations" does not exist` -- which is exactly how
-- this PR's `verify` check failed on every run before this guard. In prod push.py has always
-- created the table before applying any migration, so the REVOKE runs there.
DO $$
BEGIN
    IF to_regclass('public._migrations') IS NOT NULL THEN
        REVOKE ALL ON public._migrations FROM PUBLIC, anon, authenticated;
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- Postconditions (Session 15d) -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py. Each holds against the FINAL
-- schema state (a later migration must not undo it), in the CI build and in production.
-- ---------------------------------------------------------------------------
-- @postcondition: migrations_ledger_closed_to_anon_and_authenticated
-- SQL: SELECT to_regclass('public._migrations') IS NOT NULL AND count(*) = 7 AND bool_and(NOT
-- SQL: has_table_privilege('anon', 'public._migrations', p) AND NOT
-- SQL: has_table_privilege('authenticated', 'public._migrations', p)) FROM
-- SQL: unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) AS p
