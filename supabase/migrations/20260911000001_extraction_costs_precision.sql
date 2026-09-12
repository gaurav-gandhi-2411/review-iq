-- Migration: declare extraction_costs.cost_usd/cost_inr's real precision explicitly.
--
-- Why: the original capture migration (20260710235958_capture_extraction_costs.sql,
-- PR #89, "capture out-of-band tables") recorded these two columns as bare NUMERIC
-- (no precision/scale) -- an incomplete capture, not a real production difference.
-- Production has always had NUMERIC(14,8)/NUMERIC(14,6) on these columns (the table
-- was created out-of-band, outside the migration workflow, before being captured) --
-- confirmed by direct live query, 2026-09-10 (Session 7 P4) and re-confirmed
-- immediately before this migration was written, 2026-09-11 (Session 9 P2b):
--
--   cost_usd: numeric, precision=14, scale=8
--   cost_inr: numeric, precision=14, scale=6
--
-- This migration changes nothing about live production data or behavior -- it makes
-- the MIGRATION HISTORY match what has always been true in production, closing the
-- gap scripts/check_schema_drift.py's nightly run has been reporting (PR #146 fixed
-- the ~130 other false-positive diffs from this same root cause; these two were the
-- 2 real, deliberately-untouched diffs that PR left for a dedicated migration).
--
-- Safety: extraction_costs is confirmed EMPTY (0 rows, re-verified immediately before
-- writing this migration) and has zero application code writing to it yet (Session 7/8
-- finding, re-confirmed) -- ALTER COLUMN TYPE with zero rows is a metadata-only
-- operation, no table rewrite, no data risk. Idempotent: re-running this against an
-- already-corrected column (same target type) is a no-op change, not an error.

ALTER TABLE public.extraction_costs
    ALTER COLUMN cost_usd TYPE NUMERIC(14, 8);

ALTER TABLE public.extraction_costs
    ALTER COLUMN cost_inr TYPE NUMERIC(14, 6);
