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
