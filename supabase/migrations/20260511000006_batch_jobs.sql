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

-- ---------------------------------------------------------------------------
-- Postconditions (Session 15d) -- verified by supabase/push.py before this file is ledgered and by
-- `push.py --verify`; grammar in supabase/postconditions.py. Each holds against the FINAL
-- schema state (a later migration must not undo it), in the CI build and in production.
-- ---------------------------------------------------------------------------
-- @postcondition: batch_jobs_columns_and_key
-- SQL: SELECT (SELECT count(*) FROM pg_attribute a WHERE a.attrelid = 'public.batch_jobs'::regclass
-- SQL: AND a.attnum > 0 AND NOT a.attisdropped AND (a.attname, format_type(a.atttypid,
-- SQL: a.atttypmod), a.attnotnull) IN (('job_id', 'text', true), ('org_id', 'uuid', true),
-- SQL: ('status', 'text', true), ('total', 'integer', true), ('processed', 'integer', true),
-- SQL: ('failed', 'integer', true), ('source_columns', 'text', false), ('created_at',
-- SQL: 'timestamp with time zone', true), ('completed_at', 'timestamp with time zone', false))) = 9
-- SQL: AND EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conrelid = 'public.batch_jobs'::regclass
-- SQL: AND c.contype = 'p' AND pg_get_constraintdef(c.oid) = 'PRIMARY KEY (job_id)') AND EXISTS
-- SQL: (SELECT 1 FROM pg_constraint c WHERE c.conrelid = 'public.batch_jobs'::regclass AND
-- SQL: c.contype = 'c' AND pg_get_constraintdef(c.oid) LIKE '%processing%')

-- @postcondition: batch_jobs_org_fk_and_index
-- SQL: SELECT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conrelid =
-- SQL: 'public.batch_jobs'::regclass AND c.contype = 'f' AND c.confrelid =
-- SQL: 'public.organizations'::regclass AND c.confdeltype = 'c') AND EXISTS (SELECT 1 FROM pg_index
-- SQL: i WHERE i.indexrelid = to_regclass('public.idx_batch_jobs_org_id') AND i.indrelid =
-- SQL: 'public.batch_jobs'::regclass)
