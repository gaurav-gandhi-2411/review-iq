-- Migration: email_suppressions -- addresses that hard-bounced or complained (S20 M3b item 4).
--
-- Why: alert/digest mail goes out through Resend with no feedback loop. Repeatedly mailing a
-- dead or complaining address damages the sending domain's reputation. POST /webhooks/resend
-- (app/api/webhooks/resend.py) receives email.bounced (Permanent) / email.complained events
-- and records them here; the alert and digest senders consult this table before sending.
--
-- Expand-only: a new table plus three new functions, no existing object is altered.
--
-- Data minimisation: the address itself is never stored -- only sha256(lower(trim(address))),
-- which is all the sender check needs. org_id is a best-effort mapping (NULL when no
-- organization currently has that notification_email) and is SET NULL if the org is deleted.
--
-- Access model: the table is NOT readable by anon/authenticated/service_role/review_iq_app.
-- RLS is enabled with no policies (deny by default). The app reaches it only through three
-- narrow SECURITY DEFINER functions owned by review_iq_migrator (same pattern as the
-- tenant resolvers in 20260801000002), because the webhook has no tenant context and
-- review_iq_app does not hold BYPASSRLS (ADR 0006).
--
-- Idempotent replay: event_id is UNIQUE (svix-id of the webhook delivery plus the recipient
-- index); a replayed delivery inserts nothing.
--
-- Rollback: DROP the three functions then the table (it holds only derived suppression data,
-- which a later Resend event would repopulate).

CREATE TABLE IF NOT EXISTS public.email_suppressions (
    id         UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     UUID         REFERENCES public.organizations (id) ON DELETE SET NULL,
    email_hash TEXT         NOT NULL CHECK (email_hash ~ '^[0-9a-f]{64}$'),
    reason     TEXT         NOT NULL CHECK (reason IN ('hard_bounce', 'complaint')),
    event_id   TEXT         NOT NULL CHECK (char_length(event_id) BETWEEN 1 AND 200),
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT email_suppressions_event_id_key UNIQUE (event_id)
);

CREATE INDEX IF NOT EXISTS email_suppressions_email_hash_idx
    ON public.email_suppressions (email_hash);

ALTER TABLE public.email_suppressions ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.email_suppressions FROM PUBLIC;
REVOKE ALL ON public.email_suppressions FROM anon;
REVOKE ALL ON public.email_suppressions FROM authenticated;
REVOKE ALL ON public.email_suppressions FROM service_role;
REVOKE ALL ON public.email_suppressions FROM review_iq_app;

-- 1. record_email_suppression -- insert one suppression; returns true if newly inserted,
--    false on a replayed event_id. Maps the address to an org (oldest match) if any.
CREATE OR REPLACE FUNCTION public.record_email_suppression(
    p_event_id text, p_email text, p_reason text
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_hash   text := encode(sha256(convert_to(lower(btrim(p_email)), 'UTF8')), 'hex');
    v_org_id uuid;
    v_rows   integer;
BEGIN
    SELECT id INTO v_org_id
    FROM public.organizations
    WHERE lower(btrim(notification_email)) = lower(btrim(p_email))
    ORDER BY created_at
    LIMIT 1;

    INSERT INTO public.email_suppressions (org_id, email_hash, reason, event_id)
    VALUES (v_org_id, v_hash, p_reason, p_event_id)
    ON CONFLICT (event_id) DO NOTHING;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN v_rows = 1;
END;
$$;

-- 2. resolve_orgs_for_notification_email -- org ids whose notification_email equals the
--    address, so the caller can clear each through the single choke point
--    (set_org_notification_email_pg). Returns ONLY ids.
CREATE OR REPLACE FUNCTION public.resolve_orgs_for_notification_email(p_email text)
RETURNS SETOF uuid
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
STABLE
AS $$
    SELECT id
    FROM public.organizations
    WHERE lower(btrim(notification_email)) = lower(btrim(p_email));
$$;

-- 3. is_email_suppressed -- sender-side check; returns a boolean only.
CREATE OR REPLACE FUNCTION public.is_email_suppressed(p_email text)
RETURNS boolean
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
STABLE
AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.email_suppressions
        WHERE email_hash = encode(sha256(convert_to(lower(btrim(p_email)), 'UTF8')), 'hex')
    );
$$;

ALTER FUNCTION public.record_email_suppression(text, text, text) OWNER TO review_iq_migrator;
ALTER FUNCTION public.resolve_orgs_for_notification_email(text) OWNER TO review_iq_migrator;
ALTER FUNCTION public.is_email_suppressed(text) OWNER TO review_iq_migrator;

REVOKE ALL ON FUNCTION public.record_email_suppression(text, text, text)
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.resolve_orgs_for_notification_email(text)
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.is_email_suppressed(text)
    FROM PUBLIC, anon, authenticated, service_role;

GRANT EXECUTE ON FUNCTION public.record_email_suppression(text, text, text) TO review_iq_app;
GRANT EXECUTE ON FUNCTION public.resolve_orgs_for_notification_email(text) TO review_iq_app;
GRANT EXECUTE ON FUNCTION public.is_email_suppressed(text) TO review_iq_app;

-- ---------------------------------------------------------------------------
-- Postconditions -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py.
-- ---------------------------------------------------------------------------
-- @postcondition: email_suppressions_columns_and_unique_event_id
-- SQL: SELECT (SELECT count(*) FROM pg_attribute a WHERE a.attrelid =
-- SQL: 'public.email_suppressions'::regclass AND a.attnum > 0 AND NOT a.attisdropped AND
-- SQL: (a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull) IN (('id', 'uuid', true),
-- SQL: ('org_id', 'uuid', false), ('email_hash', 'text', true), ('reason', 'text', true),
-- SQL: ('event_id', 'text', true), ('created_at', 'timestamp with time zone', true))) = 6 AND
-- SQL: EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conrelid = 'public.email_suppressions'::regclass
-- SQL: AND c.conname = 'email_suppressions_event_id_key' AND c.contype = 'u')

-- @postcondition: email_suppressions_rls_enabled_no_policies
-- SQL: SELECT (SELECT relrowsecurity FROM pg_class WHERE oid = 'public.email_suppressions'::regclass)
-- SQL: AND NOT EXISTS (SELECT 1 FROM pg_policies p WHERE p.schemaname = 'public' AND
-- SQL: p.tablename = 'email_suppressions')

-- @postcondition: email_suppressions_no_role_can_touch_the_table
-- SQL: SELECT count(*) = 7 AND bool_and(NOT has_table_privilege('anon', 'public.email_suppressions', p)
-- SQL: AND NOT has_table_privilege('authenticated', 'public.email_suppressions', p)
-- SQL: AND NOT has_table_privilege('service_role', 'public.email_suppressions', p)
-- SQL: AND NOT has_table_privilege('review_iq_app', 'public.email_suppressions', p))
-- SQL: FROM unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) AS p

-- @postcondition: email_suppressions_functions_definer_owned_and_app_only
-- SQL: SELECT count(*) = 3 AND bool_and(pg_get_userbyid(p.proowner) = 'review_iq_migrator'
-- SQL: AND p.prosecdef AND has_function_privilege('review_iq_app', p.oid, 'EXECUTE')
-- SQL: AND NOT has_function_privilege('anon', p.oid, 'EXECUTE')
-- SQL: AND NOT has_function_privilege('authenticated', p.oid, 'EXECUTE')
-- SQL: AND NOT has_function_privilege('service_role', p.oid, 'EXECUTE'))
-- SQL: FROM pg_proc p WHERE p.pronamespace = 'public'::regnamespace AND p.proname IN
-- SQL: ('record_email_suppression', 'resolve_orgs_for_notification_email', 'is_email_suppressed')
