-- Migration: batch_job_rows — durable per-row queue for bulk extraction (Option B
-- of the CSV-throttling fix; Option A, the call-layer bulk rate limiter, is the
-- in-process safety net this builds on).
-- Rows are enqueued at upload time and drained by the tick worker
-- (POST /internal/ingest/tick) a few rows per tick, so bulk ingestion survives
-- Cloud Run restarts/scale-down and structurally cannot starve interactive
-- /v2/extract (2026-07-07 incident).
-- Idempotent: IF NOT EXISTS / DROP POLICY IF EXISTS — matches the batch_jobs
-- migration pattern (20260511000006 + 20260613000001).
-- DDL approved by GG 2026-07-09.

CREATE TABLE IF NOT EXISTS public.batch_job_rows (
    job_id      text        NOT NULL REFERENCES public.batch_jobs(job_id) ON DELETE CASCADE,
    row_index   integer     NOT NULL CHECK (row_index >= 0),
    org_id      uuid        NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    text        text        NOT NULL,
    input_hash  text,
    status      text        NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'done', 'failed')),
    error       text,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, row_index)
);

-- The tick worker's drain query: oldest pending rows first (roughly fair across
-- tenants at current scale); partial index keeps it cheap as done rows accumulate.
CREATE INDEX IF NOT EXISTS idx_batch_job_rows_pending
    ON public.batch_job_rows(status, updated_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_batch_job_rows_org_id ON public.batch_job_rows(org_id);

GRANT SELECT, INSERT, UPDATE ON public.batch_job_rows TO authenticated;

ALTER TABLE public.batch_job_rows ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  DROP POLICY IF EXISTS "batch_job_rows_authenticated_all" ON public.batch_job_rows;
  DROP POLICY IF EXISTS "batch_job_rows_anon_deny"         ON public.batch_job_rows;
END $$;

CREATE POLICY "batch_job_rows_authenticated_all" ON public.batch_job_rows
  FOR ALL TO authenticated
  USING     (org_id = public.current_org_id())
  WITH CHECK (org_id = public.current_org_id());

CREATE POLICY "batch_job_rows_anon_deny" ON public.batch_job_rows
  FOR ALL TO anon USING (false);

-- ---------------------------------------------------------------------------
-- Postconditions (Session 15d) -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py. Each holds against the FINAL
-- schema state (a later migration must not undo it), in the CI build and in production.
-- ---------------------------------------------------------------------------
-- @postcondition: batch_job_rows_columns_and_keys
-- SQL: SELECT (SELECT count(*) FROM pg_attribute a WHERE a.attrelid =
-- SQL: 'public.batch_job_rows'::regclass AND a.attnum > 0 AND NOT a.attisdropped AND (a.attname,
-- SQL: format_type(a.atttypid, a.atttypmod), a.attnotnull) IN (('job_id', 'text', true),
-- SQL: ('row_index', 'integer', true), ('org_id', 'uuid', true), ('text', 'text', true),
-- SQL: ('input_hash', 'text', false), ('status', 'text', true), ('error', 'text', false),
-- SQL: ('updated_at', 'timestamp with time zone', true))) = 8 AND EXISTS (SELECT 1 FROM
-- SQL: pg_constraint c WHERE c.conrelid = 'public.batch_job_rows'::regclass AND c.contype = 'p' AND
-- SQL: pg_get_constraintdef(c.oid) = 'PRIMARY KEY (job_id, row_index)') AND EXISTS (SELECT 1 FROM
-- SQL: pg_constraint c WHERE c.conrelid = 'public.batch_job_rows'::regclass AND c.contype = 'f' AND
-- SQL: c.confrelid = 'public.batch_jobs'::regclass AND c.confdeltype = 'c') AND EXISTS (SELECT 1
-- SQL: FROM pg_constraint c WHERE c.conrelid = 'public.batch_job_rows'::regclass AND c.contype =
-- SQL: 'f' AND c.confrelid = 'public.organizations'::regclass AND c.confdeltype = 'c')

-- @postcondition: batch_job_rows_indexes
-- SQL: SELECT EXISTS (SELECT 1 FROM pg_index i WHERE i.indexrelid =
-- SQL: to_regclass('public.idx_batch_job_rows_pending') AND i.indrelid =
-- SQL: 'public.batch_job_rows'::regclass AND i.indpred IS NOT NULL) AND EXISTS (SELECT 1 FROM
-- SQL: pg_index i WHERE i.indexrelid = to_regclass('public.idx_batch_job_rows_org_id') AND
-- SQL: i.indrelid = 'public.batch_job_rows'::regclass)

-- @postcondition: batch_job_rows_rls_and_policies
-- SQL: SELECT (SELECT count(*) = 1 AND bool_and(c.relrowsecurity) FROM pg_class c WHERE c.oid IN
-- SQL: ('public.batch_job_rows'::regclass)) AND (SELECT count(*) FROM pg_policies p WHERE
-- SQL: p.schemaname = 'public' AND (p.tablename, p.policyname, p.cmd, p.roles::text) IN
-- SQL: (('batch_job_rows', 'batch_job_rows_authenticated_all', 'ALL', '{authenticated}'),
-- SQL: ('batch_job_rows', 'batch_job_rows_anon_deny', 'ALL', '{anon}'))) = 2 AND (SELECT count(*)
-- SQL: FROM pg_policies p WHERE p.schemaname = 'public' AND p.policyname IN
-- SQL: ('batch_job_rows_authenticated_all') AND p.qual LIKE '%current_org_id()%' AND p.with_check
-- SQL: LIKE '%current_org_id()%') = 1 AND (SELECT count(*) FROM pg_policies p WHERE p.schemaname =
-- SQL: 'public' AND p.policyname IN ('batch_job_rows_anon_deny') AND p.qual = 'false') = 1
