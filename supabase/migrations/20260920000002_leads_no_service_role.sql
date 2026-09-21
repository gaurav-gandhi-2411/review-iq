-- Migration: least privilege on lead PII -- service_role holds NO privilege on public.leads.
--
-- Why: public.leads carries prospects' names, emails and free-text messages (PII). Its only
-- runtime reader/writer is review_iq_app, through a deliberately narrow grant set
-- (20260920000001_leads.sql); operators read leads with the postgres role in the Supabase
-- dashboard. Nothing in this repo reads or writes leads as service_role. Where Supabase's platform
-- default privileges hand service_role ALL on every new table (the CI build's
-- supabase/ci/bootstrap_supabase_roles.sql reproduces that), leads would silently carry a
-- broad standing grant to a role that has no reason to touch it. Revoking it is least
-- privilege (rule 96), and it makes the CI build agree with production, where the
-- migrator-created table never received that default grant (this was a standing
-- schema-drift-check finding class, see 20260912000003 finding 2 for the mirror image).
--
-- Idempotent: REVOKE of a privilege that is not held is a no-op. It is also a no-op -- WITHOUT
-- error -- when the running role does not own public.leads, which is exactly why this file
-- carries a postcondition: push.py will not ledger it unless service_role really ends up
-- with nothing.
--
-- Blast radius: the only service_role use in app/ is app/auth/signup.py's Supabase Auth
-- client (auth admin API), which never touches public.leads (grepped 2026-09-20); leads is
-- read/written only over review_iq_app's psycopg2 connection (app/core/storage_pg.py).
-- Rollback: GRANT the needed privileges back to service_role.

REVOKE ALL ON public.leads FROM service_role;

-- ---------------------------------------------------------------------------
-- Postconditions (Session 15d) -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py. Each holds against the FINAL
-- schema state (a later migration must not undo it), in the CI build and in production.
-- ---------------------------------------------------------------------------
-- @postcondition: leads_service_role_has_no_table_privileges
-- SQL: SELECT count(*) = 7 AND bool_and(NOT has_table_privilege('service_role',
-- SQL: 'public.leads', p)) FROM unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE',
-- SQL: 'REFERENCES','TRIGGER']) AS p

-- @postcondition: leads_service_role_has_no_column_privileges
-- SQL: SELECT count(*) = 4 AND bool_and(NOT has_any_column_privilege('service_role',
-- SQL: 'public.leads', p)) FROM unnest(ARRAY['SELECT','INSERT','UPDATE','REFERENCES']) AS p
