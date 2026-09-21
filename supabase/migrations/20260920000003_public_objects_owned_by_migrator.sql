-- Migration: every table/sequence/view/function in schema public is owned by review_iq_migrator.
--
-- Why: supabase/push.py runs as review_iq_migrator, but most public objects were created by
-- `postgres` and stayed owned by it. GRANT/REVOKE/ALTER by a non-owner either fails ("must be
-- owner of ...") or -- worse -- completes WITHOUT ERROR and changes NOTHING: 20260912000003's
-- `REVOKE EXECUTE ON FUNCTION public.current_org_id() FROM PUBLIC, anon` did exactly that in
-- production (verified 2026-09-20: anon could still EXECUTE after the file "succeeded"), and
-- the ledger would have recorded it as applied. The ownership was fixed by hand in the
-- Supabase SQL Editor (all 17 public tables + public.current_org_id() -> review_iq_migrator);
-- this file codifies that change so a from-scratch build (CI, disaster recovery, a new
-- environment) reaches the same state instead of depending on a hand-run statement.
--
-- Behavior:
--   * Run as a superuser or as `postgres` (the role that owns the objects; a member of
--     review_iq_migrator since 20260801000001, which ALTER ... OWNER TO requires): every
--     public table/sequence/view/materialized view/foreign table/function/procedure not
--     already owned by review_iq_migrator is transferred to it. Extension-owned objects are
--     skipped, and so are sequences that belong to a table column (they follow their table).
--   * Run as review_iq_migrator (i.e. through push.py in production once ownership is already
--     right): a deliberate NO-OP with a NOTICE -- a non-owner cannot transfer what it does
--     not own, and pretending otherwise is the failure this file exists to fix. The
--     postconditions below are what make that no-op honest: push.py refuses to ledger the
--     file unless nothing in public is still owned by anyone else.
--   * Idempotent: a second run finds nothing to transfer.
--
-- Blast radius: ownership only; no grants, policies, data or definitions change (ALTER OWNER
-- rewrites the owner's own ACL entries to the new owner and leaves every other grantee
-- untouched). Functions that are SECURITY DEFINER keep their behavior: the resolver
-- functions were already owned by review_iq_migrator; public.current_org_id() only reads
-- session settings. Rollback: ALTER ... OWNER TO postgres for the affected objects.

DO $$
DECLARE
    v_owner CONSTANT name := 'review_iq_migrator';
    r RECORD;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_owner) THEN
        RAISE EXCEPTION 'role % does not exist; 20260801000001_role_separation_bypassrls_remediation.sql must be applied first', v_owner;
    END IF;

    IF NOT (COALESCE((SELECT rolsuper FROM pg_roles WHERE rolname = current_user), false)
            OR current_user = 'postgres') THEN
        RAISE NOTICE 'running as %, which is neither a superuser nor postgres: skipping the ownership transfer (the postconditions verify the result)', current_user;
        RETURN;
    END IF;

    FOR r IN
        SELECT format('ALTER %s %I.%I OWNER TO %I',
                      CASE c.relkind
                          WHEN 'S' THEN 'SEQUENCE'
                          WHEN 'v' THEN 'VIEW'
                          WHEN 'm' THEN 'MATERIALIZED VIEW'
                          WHEN 'f' THEN 'FOREIGN TABLE'
                          ELSE 'TABLE'
                      END,
                      n.nspname, c.relname, v_owner) AS stmt
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
          AND pg_get_userbyid(c.relowner) <> v_owner
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend d
              WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e'
          )
          AND NOT (c.relkind = 'S' AND EXISTS (
              SELECT 1 FROM pg_depend d
              WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid
                AND d.deptype IN ('a', 'i')
          ))
        ORDER BY c.relname
    LOOP
        EXECUTE r.stmt;
    END LOOP;

    FOR r IN
        SELECT format('ALTER %s %I.%I(%s) OWNER TO %I',
                      CASE p.prokind WHEN 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END,
                      n.nspname, p.proname, pg_get_function_identity_arguments(p.oid),
                      v_owner) AS stmt
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.prokind IN ('f', 'p')
          AND pg_get_userbyid(p.proowner) <> v_owner
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend d
              WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e'
          )
        ORDER BY p.proname
    LOOP
        EXECUTE r.stmt;
    END LOOP;
END
$$;

-- ---------------------------------------------------------------------------
-- Postconditions (Session 15d) -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py. Each holds against the FINAL
-- schema state (a later migration must not undo it), in the CI build and in production.
-- ---------------------------------------------------------------------------
-- @postcondition: public_tables_sequences_views_owned_by_migrator
-- SQL: SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'review_iq_migrator')
-- SQL: AND pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid =
-- SQL: 'public.organizations'::regclass)) = 'review_iq_migrator'
-- SQL: AND NOT EXISTS (SELECT 1 FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace
-- SQL: AND c.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
-- SQL: AND pg_get_userbyid(c.relowner) <> 'review_iq_migrator'
-- SQL: AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass
-- SQL: AND d.objid = c.oid AND d.deptype = 'e')
-- SQL: AND NOT (c.relkind = 'S' AND EXISTS (SELECT 1 FROM pg_depend d
-- SQL: WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype IN ('a', 'i'))))

-- @postcondition: public_functions_owned_by_migrator
-- SQL: SELECT EXISTS (SELECT 1 FROM pg_proc WHERE oid = to_regprocedure('public.current_org_id()'))
-- SQL: AND NOT EXISTS (SELECT 1 FROM pg_proc p WHERE p.pronamespace = 'public'::regnamespace
-- SQL: AND p.prokind IN ('f', 'p') AND pg_get_userbyid(p.proowner) <> 'review_iq_migrator'
-- SQL: AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_proc'::regclass
-- SQL: AND d.objid = p.oid AND d.deptype = 'e'))

-- @postcondition: current_org_id_owned_by_migrator
-- SQL: SELECT (SELECT pg_get_userbyid(proowner) FROM pg_proc
-- SQL: WHERE oid = to_regprocedure('public.current_org_id()')) = 'review_iq_migrator'
