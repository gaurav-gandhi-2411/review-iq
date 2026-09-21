-- Migration: alert_preferences + alert_log tables for the alerting engine.
--
-- RLS: identical pattern to corrections (20260619000003_corrections.sql):
--   authenticated → FOR ALL USING/WITH CHECK (org_id = public.current_org_id())
--   anon          → FOR ALL USING (false)
--
-- NOTE: public.current_org_id() is already defined in 20260510000002_rls_policies.sql.
-- This migration does NOT redefine it.
--
-- alert_preferences: org configures which event types + frequency. Grants SELECT,
--   INSERT, UPDATE (no DELETE — prefs are toggled, never destroyed).
--
-- alert_log: append-only audit + dedupe log. Grants SELECT, INSERT only; separate
--   per-op policies make the append-only constraint explicit at the RLS layer.
--
-- Idempotent: CREATE TABLE/INDEX use IF NOT EXISTS; policy block drops before recreating.

-- ---------------------------------------------------------------------------
-- alert_preferences
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.alert_preferences (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID        NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
    event_type  TEXT        NOT NULL
                CHECK (event_type IN ('high_urgency', 'likely_fake', 'fake_cluster', 'topic_spike')),
    enabled     BOOLEAN     NOT NULL DEFAULT true,
    frequency   TEXT        NOT NULL DEFAULT 'immediate'
                CHECK (frequency IN ('immediate', 'daily_digest')),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, event_type)
);

CREATE INDEX IF NOT EXISTS idx_alert_prefs_org_id
    ON public.alert_preferences (org_id);

-- No DELETE: prefs are toggled (enabled=false), never removed.
GRANT SELECT, INSERT, UPDATE ON public.alert_preferences TO authenticated;

ALTER TABLE public.alert_preferences ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    DROP POLICY IF EXISTS "alert_prefs_authenticated_all" ON public.alert_preferences;
    DROP POLICY IF EXISTS "alert_prefs_anon_deny"         ON public.alert_preferences;
END $$;

-- WITH CHECK mandatory: INSERT bypasses USING; omitting it lets a tenant write
-- preferences under another org's org_id while still reading only their own rows.
CREATE POLICY "alert_prefs_authenticated_all" ON public.alert_preferences
    FOR ALL TO authenticated
    USING     (org_id = public.current_org_id())
    WITH CHECK (org_id = public.current_org_id());

CREATE POLICY "alert_prefs_anon_deny" ON public.alert_preferences
    FOR ALL TO anon USING (false);

-- ---------------------------------------------------------------------------
-- alert_log
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.alert_log (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID        NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
    review_id   TEXT,                          -- NULL for cluster/spike events (no single review)
    event_type  TEXT        NOT NULL,
    sent_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    details     JSONB       NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_alert_log_org_event_sent
    ON public.alert_log (org_id, event_type, sent_at DESC);

CREATE INDEX IF NOT EXISTS idx_alert_log_org_review
    ON public.alert_log (org_id, review_id) WHERE review_id IS NOT NULL;

-- Append-only: no UPDATE/DELETE granted at all.
-- UPDATE/DELETE by authenticated will fail at the grant layer before RLS runs —
-- stronger guarantee than RLS alone.
GRANT SELECT, INSERT ON public.alert_log TO authenticated;

ALTER TABLE public.alert_log ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    DROP POLICY IF EXISTS "alert_log_authenticated_select" ON public.alert_log;
    DROP POLICY IF EXISTS "alert_log_authenticated_insert" ON public.alert_log;
    DROP POLICY IF EXISTS "alert_log_anon_deny"            ON public.alert_log;
END $$;

-- Separate per-op policies: makes append-only constraint visible at the RLS layer.
-- A FOR ALL policy covering UPDATE/DELETE would be misleading when those ops are
-- denied by grant anyway.
CREATE POLICY "alert_log_authenticated_select" ON public.alert_log
    FOR SELECT TO authenticated
    USING (org_id = public.current_org_id());

CREATE POLICY "alert_log_authenticated_insert" ON public.alert_log
    FOR INSERT TO authenticated
    WITH CHECK (org_id = public.current_org_id());

CREATE POLICY "alert_log_anon_deny" ON public.alert_log
    FOR ALL TO anon USING (false);

-- ---------------------------------------------------------------------------
-- Postconditions (Session 15d) -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py. Each holds against the FINAL
-- schema state (a later migration must not undo it), in the CI build and in production.
-- ---------------------------------------------------------------------------
-- @postcondition: alert_preferences_columns_and_constraints
-- SQL: SELECT (SELECT count(*) FROM pg_attribute a WHERE a.attrelid =
-- SQL: 'public.alert_preferences'::regclass AND a.attnum > 0 AND NOT a.attisdropped AND (a.attname,
-- SQL: format_type(a.atttypid, a.atttypmod), a.attnotnull) IN (('id', 'uuid', true), ('org_id',
-- SQL: 'uuid', true), ('event_type', 'text', true), ('enabled', 'boolean', true), ('frequency',
-- SQL: 'text', true), ('updated_at', 'timestamp with time zone', true))) = 6 AND EXISTS (SELECT 1
-- SQL: FROM pg_constraint c WHERE c.conrelid = 'public.alert_preferences'::regclass AND c.contype =
-- SQL: 'u' AND pg_get_constraintdef(c.oid) = 'UNIQUE (org_id, event_type)') AND EXISTS (SELECT 1
-- SQL: FROM pg_constraint c WHERE c.conrelid = 'public.alert_preferences'::regclass AND c.contype =
-- SQL: 'c' AND pg_get_constraintdef(c.oid) LIKE '%daily_digest%') AND EXISTS (SELECT 1 FROM
-- SQL: pg_constraint c WHERE c.conrelid = 'public.alert_preferences'::regclass AND c.contype = 'f'
-- SQL: AND c.confrelid = 'public.organizations'::regclass AND c.confdeltype = 'c') AND EXISTS
-- SQL: (SELECT 1 FROM pg_index i WHERE i.indexrelid = to_regclass('public.idx_alert_prefs_org_id')
-- SQL: AND i.indrelid = 'public.alert_preferences'::regclass)

-- @postcondition: alert_log_columns_and_indexes
-- SQL: SELECT (SELECT count(*) FROM pg_attribute a WHERE a.attrelid = 'public.alert_log'::regclass
-- SQL: AND a.attnum > 0 AND NOT a.attisdropped AND (a.attname, format_type(a.atttypid,
-- SQL: a.atttypmod), a.attnotnull) IN (('id', 'uuid', true), ('org_id', 'uuid', true),
-- SQL: ('review_id', 'text', false), ('event_type', 'text', true), ('sent_at',
-- SQL: 'timestamp with time zone', true), ('details', 'jsonb', true))) = 6 AND EXISTS (SELECT 1
-- SQL: FROM pg_constraint c WHERE c.conrelid = 'public.alert_log'::regclass AND c.contype = 'f' AND
-- SQL: c.confrelid = 'public.organizations'::regclass AND c.confdeltype = 'c') AND EXISTS (SELECT 1
-- SQL: FROM pg_index i WHERE i.indexrelid = to_regclass('public.idx_alert_log_org_event_sent') AND
-- SQL: i.indrelid = 'public.alert_log'::regclass) AND EXISTS (SELECT 1 FROM pg_index i WHERE
-- SQL: i.indexrelid = to_regclass('public.idx_alert_log_org_review') AND i.indrelid =
-- SQL: 'public.alert_log'::regclass AND i.indpred IS NOT NULL)

-- @postcondition: alert_tables_rls_and_policies
-- SQL: SELECT (SELECT count(*) = 2 AND bool_and(c.relrowsecurity) FROM pg_class c WHERE c.oid IN
-- SQL: ('public.alert_preferences'::regclass, 'public.alert_log'::regclass)) AND (SELECT count(*)
-- SQL: FROM pg_policies p WHERE p.schemaname = 'public' AND (p.tablename, p.policyname, p.cmd,
-- SQL: p.roles::text) IN (('alert_preferences', 'alert_prefs_authenticated_all', 'ALL',
-- SQL: '{authenticated}'), ('alert_preferences', 'alert_prefs_anon_deny', 'ALL', '{anon}'),
-- SQL: ('alert_log', 'alert_log_authenticated_select', 'SELECT', '{authenticated}'), ('alert_log',
-- SQL: 'alert_log_authenticated_insert', 'INSERT', '{authenticated}'), ('alert_log',
-- SQL: 'alert_log_anon_deny', 'ALL', '{anon}'))) = 5 AND (SELECT count(*) FROM pg_policies p WHERE
-- SQL: p.schemaname = 'public' AND p.policyname IN ('alert_prefs_authenticated_all') AND p.qual
-- SQL: LIKE '%current_org_id()%' AND p.with_check LIKE '%current_org_id()%') = 1 AND (SELECT
-- SQL: count(*) FROM pg_policies p WHERE p.schemaname = 'public' AND p.policyname IN
-- SQL: ('alert_prefs_anon_deny', 'alert_log_anon_deny') AND p.qual = 'false') = 2 AND (SELECT
-- SQL: count(*) FROM pg_policies p WHERE p.schemaname = 'public' AND p.policyname IN
-- SQL: ('alert_log_authenticated_select') AND p.qual LIKE '%current_org_id()%') = 1 AND (SELECT
-- SQL: count(*) FROM pg_policies p WHERE p.schemaname = 'public' AND p.policyname =
-- SQL: 'alert_log_authenticated_insert' AND p.with_check LIKE '%current_org_id()%') = 1
