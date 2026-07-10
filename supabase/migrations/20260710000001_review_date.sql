-- Migration: review_date + product plumbing for Phase 2 temporal detectors (batch-defect,
-- trend). Audit found no ingestion path captured a review's ORIGINAL date -- only ingestion
-- time -- which would make those detectors produce fake spikes on bulk historical CSV uploads.
-- Plan approved by GG 2026-07-10 (see C:\Users\gaura\.claude\plans\zany-fluttering-kahan.md).
--
-- Additive nullable columns only -- no RLS policy changes needed, existing org_id-scoped
-- policies on both tables already cover new columns (RLS is row-level, not column-level).
-- Matches the convention in 20260511000004_extractions_flat_columns.sql. No backfill --
-- existing rows keep review_date = NULL permanently and honestly (their real date is unknown).
-- Idempotent: ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.

ALTER TABLE public.extractions
  ADD COLUMN IF NOT EXISTS review_date timestamptz;

ALTER TABLE public.batch_job_rows
  ADD COLUMN IF NOT EXISTS product      text,
  ADD COLUMN IF NOT EXISTS review_date  timestamptz;

-- Partial index: most existing rows will be NULL (no historical backfill), so this stays cheap
-- while making the detectors' future `WHERE review_date IS NOT NULL` scan fast.
CREATE INDEX IF NOT EXISTS idx_extractions_review_date
  ON public.extractions (org_id, review_date) WHERE review_date IS NOT NULL;
