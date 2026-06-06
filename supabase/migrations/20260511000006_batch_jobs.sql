-- Migration: batch_jobs table for v2 CSV ingest job tracking
-- Idempotent: uses IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS public.batch_jobs (
    job_id         text        PRIMARY KEY,
    org_id         uuid        NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    status         text        NOT NULL DEFAULT 'pending'
                               CHECK (status IN ('pending', 'processing', 'done', 'failed')),
    total          integer     NOT NULL CHECK (total >= 0),
    processed      integer     NOT NULL DEFAULT 0 CHECK (processed >= 0),
    failed         integer     NOT NULL DEFAULT 0 CHECK (failed >= 0),
    source_columns text,       -- JSON: {"text_column": "...", "product_column": "...", "input_hashes": [...]}
    created_at     timestamptz NOT NULL DEFAULT now(),
    completed_at   timestamptz
);

CREATE INDEX IF NOT EXISTS idx_batch_jobs_org_id ON public.batch_jobs(org_id);

GRANT SELECT, INSERT, UPDATE ON public.batch_jobs TO authenticated;
