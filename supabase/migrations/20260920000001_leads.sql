-- Migration: leads -- marketing-site lead capture (POST /leads, Session 15c C9).
--
-- Why this exists: the marketing site's two "contact us" CTAs were mailto: links, which
-- lose the lead entirely if the visitor has no mail client configured and leave no record
-- of who asked. POST /leads (app/api/leads.py) replaces them: validate, persist here,
-- then notify hello@samidhareviews.xyz and confirm to the submitter via Resend.
--
-- Pre-tenant data: a lead has no org (the visitor is not a customer yet), so there is
-- nothing for _set_tenant() to scope to -- see the scripts/check_undocumented_pg_connects.py
-- ALLOWLIST entries for insert_lead_pg / update_lead_email_status_pg. Isolation is done
-- with grants + RLS instead, following the demo_daily_usage / extraction_costs demo-row
-- pattern (20260905000001, 20260905000002): review_iq_app (NOBYPASSRLS after the S0
-- remediation, ADR 0006) gets a narrow, explicit policy and grant set; anon/authenticated
-- (the Supabase-JWT-facing roles, which review_iq_app also inherits from) get nothing.
--
-- Data minimisation: the raw client IP is never stored -- source_ip_hash is an
-- HMAC-SHA256 of the IP keyed by a server-side secret (LEADS_IP_HASH_SALT), computed in
-- app code. It exists only so an abusive source can be correlated across rows without
-- keeping a raw IP; it is NULL when the secret is unset.
--
-- No data deletion: additive migration only (CREATE ... IF NOT EXISTS, no DROP TABLE).

CREATE TABLE IF NOT EXISTS public.leads (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    name              TEXT         NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100),
    -- Deliberately loose format check (one @, a dot in the domain, no whitespace); the
    -- strict format validation lives in the API layer. This is the last-line backstop.
    email             TEXT         NOT NULL CHECK (
                                       char_length(email) BETWEEN 3 AND 254
                                       AND email ~ '^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$'
                                   ),
    company           TEXT         NOT NULL CHECK (char_length(company) BETWEEN 1 AND 150),
    brands            TEXT         NOT NULL CHECK (char_length(brands) BETWEEN 1 AND 500),
    reviews_per_month TEXT         NOT NULL CHECK (char_length(reviews_per_month) BETWEEN 1 AND 50),
    message           TEXT         CHECK (message IS NULL OR char_length(message) BETWEEN 1 AND 4000),
    source_ip_hash    TEXT         CHECK (source_ip_hash IS NULL OR source_ip_hash ~ '^[0-9a-f]{64}$'),
    user_agent        TEXT         CHECK (user_agent IS NULL OR char_length(user_agent) <= 256),
    email_status      TEXT         NOT NULL DEFAULT 'pending' CHECK (
                                       email_status IN ('pending', 'sent', 'partial', 'failed', 'skipped')
                                   ),
    -- Single-line free-text fields carry no control characters (CR/LF header-injection
    -- class); message may contain newlines and tabs only. Also enforced in the API layer.
    CONSTRAINT leads_single_line_fields_no_controls CHECK (
        (name || email || company || brands || reviews_per_month) !~ '[\x01-\x1F\x7F]'
    ),
    CONSTRAINT leads_message_controls CHECK (
        message IS NULL OR message !~ '[\x01-\x08\x0B-\x1F\x7F]'
    )
);

ALTER TABLE public.leads ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.leads FROM PUBLIC;
REVOKE ALL ON public.leads FROM anon;
REVOKE ALL ON public.leads FROM authenticated;

-- review_iq_app: INSERT the row, then flip email_status once the notification attempt is
-- done. Column-level SELECT/UPDATE keep the read/write surface to the two columns the
-- status update actually touches (its WHERE clause needs SELECT on id); the app can NEVER
-- read back name/email/company/message. Operators read leads with the postgres/service
-- role via the Supabase dashboard.
GRANT INSERT ON public.leads TO review_iq_app;
GRANT SELECT (id, email_status) ON public.leads TO review_iq_app;
GRANT UPDATE (email_status) ON public.leads TO review_iq_app;
-- No DELETE, no full-row SELECT, no UPDATE on any other column.

DROP POLICY IF EXISTS "leads_review_iq_app_insert" ON public.leads;
CREATE POLICY "leads_review_iq_app_insert" ON public.leads
    FOR INSERT TO review_iq_app
    WITH CHECK (email_status = 'pending');

-- The status update must be able to see the row it just inserted (UPDATE ... WHERE id = x
-- also evaluates the SELECT policy). Limited to the last hour so a compromised app
-- connection cannot rewrite the email_status of historical leads.
DROP POLICY IF EXISTS "leads_review_iq_app_select_recent" ON public.leads;
CREATE POLICY "leads_review_iq_app_select_recent" ON public.leads
    FOR SELECT TO review_iq_app
    USING (created_at > now() - interval '1 hour');

DROP POLICY IF EXISTS "leads_review_iq_app_update_recent" ON public.leads;
CREATE POLICY "leads_review_iq_app_update_recent" ON public.leads
    FOR UPDATE TO review_iq_app
    USING (created_at > now() - interval '1 hour')
    WITH CHECK (email_status <> 'pending');

-- ---------------------------------------------------------------------------
-- Postconditions (Session 15d) -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py. Each holds against the FINAL
-- schema state (a later migration must not undo it), in the CI build and in production.
-- ---------------------------------------------------------------------------
-- @postcondition: leads_columns
-- SQL: SELECT (SELECT count(*) FROM pg_attribute a WHERE a.attrelid = 'public.leads'::regclass AND
-- SQL: a.attnum > 0 AND NOT a.attisdropped AND (a.attname, format_type(a.atttypid, a.atttypmod),
-- SQL: a.attnotnull) IN (('id', 'uuid', true), ('created_at', 'timestamp with time zone', true),
-- SQL: ('name', 'text', true), ('email', 'text', true), ('company', 'text', true), ('brands',
-- SQL: 'text', true), ('reviews_per_month', 'text', true), ('message', 'text', false),
-- SQL: ('source_ip_hash', 'text', false), ('user_agent', 'text', false), ('email_status', 'text',
-- SQL: true))) = 11 AND EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conrelid =
-- SQL: 'public.leads'::regclass AND c.contype = 'p' AND pg_get_constraintdef(c.oid) =
-- SQL: 'PRIMARY KEY (id)')

-- @postcondition: leads_backstop_check_constraints
-- SQL: SELECT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conrelid = 'public.leads'::regclass AND
-- SQL: c.conname = 'leads_single_line_fields_no_controls' AND c.contype = 'c' AND
-- SQL: pg_get_constraintdef(c.oid) LIKE '%x01%') AND EXISTS (SELECT 1 FROM pg_constraint c WHERE
-- SQL: c.conrelid = 'public.leads'::regclass AND c.conname = 'leads_message_controls' AND c.contype
-- SQL: = 'c' AND pg_get_constraintdef(c.oid) LIKE '%x08%') AND EXISTS (SELECT 1 FROM pg_constraint
-- SQL: c WHERE c.conrelid = 'public.leads'::regclass AND c.contype = 'c' AND
-- SQL: pg_get_constraintdef(c.oid) LIKE '%pending%' AND pg_get_constraintdef(c.oid) LIKE
-- SQL: '%skipped%')

-- @postcondition: leads_rls_and_review_iq_app_only_policies
-- SQL: SELECT (SELECT count(*) = 1 AND bool_and(c.relrowsecurity) FROM pg_class c WHERE c.oid IN
-- SQL: ('public.leads'::regclass)) AND (SELECT count(*) FROM pg_policies p WHERE p.schemaname =
-- SQL: 'public' AND (p.tablename, p.policyname, p.cmd, p.roles::text) IN (('leads',
-- SQL: 'leads_review_iq_app_insert', 'INSERT', '{review_iq_app}'), ('leads',
-- SQL: 'leads_review_iq_app_select_recent', 'SELECT', '{review_iq_app}'), ('leads',
-- SQL: 'leads_review_iq_app_update_recent', 'UPDATE', '{review_iq_app}'))) = 3

-- @postcondition: leads_anon_and_authenticated_have_no_access
-- SQL: SELECT count(*) = 7 AND bool_and(NOT has_table_privilege('anon', 'public.leads', p) AND NOT
-- SQL: has_table_privilege('authenticated', 'public.leads', p)) FROM
-- SQL: unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) AS p

-- @postcondition: leads_review_iq_app_privileges_are_column_narrow
-- SQL: SELECT has_table_privilege('review_iq_app', 'public.leads', 'INSERT') AND NOT
-- SQL: has_table_privilege('review_iq_app', 'public.leads', 'SELECT') AND NOT
-- SQL: has_table_privilege('review_iq_app', 'public.leads', 'UPDATE') AND NOT
-- SQL: has_table_privilege('review_iq_app', 'public.leads', 'DELETE') AND
-- SQL: has_column_privilege('review_iq_app', 'public.leads', 'id', 'SELECT') AND
-- SQL: has_column_privilege('review_iq_app', 'public.leads', 'email_status', 'SELECT') AND NOT
-- SQL: has_column_privilege('review_iq_app', 'public.leads', 'email', 'SELECT') AND
-- SQL: has_column_privilege('review_iq_app', 'public.leads', 'email_status', 'UPDATE') AND NOT
-- SQL: has_column_privilege('review_iq_app', 'public.leads', 'name', 'UPDATE')
