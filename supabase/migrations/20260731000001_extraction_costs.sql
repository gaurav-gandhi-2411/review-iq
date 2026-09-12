-- Migration: extraction_costs — per-extraction token/cost telemetry (Wave 1 Section G).
--
-- Why a new table instead of extending usage_records: usage_records is 1:1 with an
-- authenticated API *request* (one row inserted per require_api_key call, see
-- app/auth/api_key.py._lookup_and_record), not with an actual LLM extraction. Batch/CSV
-- rows drained by app/core/ingest_worker.py never go through require_api_key at all —
-- they run with ApiKeyContext(usage_record_id="") by design (see that module's
-- SYSTEM PATH WARNING docstring) — so usage_records structurally cannot carry
-- per-extraction cost for the majority of extraction volume (bulk ingest). extractions
-- itself is the right per-extraction anchor, but adding cost columns there would mix a
-- COGS/billing concern into the customer-facing extraction record; a separate table with
-- an FK keeps the concerns apart and lets this table be queried/retained independently
-- (e.g. shorter retention than review data, or an operator-only read path).
--
-- Written by _run_extraction_v2 (app/api/v2/extract.py) — the single call site shared by
-- POST /v2/extract, POST /v2/extract/batch (via ingest_worker.drain_rows), and CSV ingest
-- (also via drain_rows) — so this table has proper per-extraction granularity across every
-- real production extraction path, unlike usage_records.
--
-- Pattern matches the most recent precedent for a new tenant table (corrections.sql,
-- 20260619000003): explicit GRANT to authenticated only (no REVOKE — this is a genuinely
-- new table, not one found live with dangerous default grants, see
-- 20260711000002_quota_requests_rls.sql for that case), RLS enabled with WITH CHECK
-- mandatory, anon denied outright.
--
-- Idempotent: CREATE TABLE / INDEX use IF NOT EXISTS; policy block drops before recreating.

-- ---------------------------------------------------------------------------
-- Table
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.extraction_costs (
    id             UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id         UUID          NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
    extraction_id  UUID          REFERENCES public.extractions (id) ON DELETE SET NULL,
    provider       TEXT          NOT NULL,
    model          TEXT          NOT NULL,
    tier           TEXT          NOT NULL,
    language       TEXT,
    tokens_in      INTEGER       NOT NULL DEFAULT 0 CHECK (tokens_in >= 0),
    tokens_out     INTEGER       NOT NULL DEFAULT 0 CHECK (tokens_out >= 0),
    cost_usd       NUMERIC(14, 8) NOT NULL CHECK (cost_usd >= 0),
    cost_inr       NUMERIC(14, 6) NOT NULL CHECK (cost_inr >= 0),
    created_at     TIMESTAMPTZ   NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Indexes — org-scoped lookups, and the cost-per-1k-per-language/tier aggregate.
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_extraction_costs_org_created_at
    ON public.extraction_costs (org_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_extraction_costs_language_tier
    ON public.extraction_costs (language, tier);

-- ---------------------------------------------------------------------------
-- Grants (authenticated role; service_role / review_iq_app BYPASSRLS need no grant)
-- ---------------------------------------------------------------------------
GRANT SELECT, INSERT ON public.extraction_costs TO authenticated;
-- No UPDATE/DELETE: write-once telemetry log, same convention as alert_log /
-- quota_requests — corrections happen by inserting a new record, not mutating history.

-- ---------------------------------------------------------------------------
-- Row-Level Security
-- ---------------------------------------------------------------------------
ALTER TABLE public.extraction_costs ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    DROP POLICY IF EXISTS "extraction_costs_authenticated_all" ON public.extraction_costs;
    DROP POLICY IF EXISTS "extraction_costs_anon_deny"         ON public.extraction_costs;
END $$;

-- WITH CHECK mandatory: INSERT bypasses USING; omitting it lets a tenant write a cost
-- row tagged with another org's org_id while still only reading their own rows back.
CREATE POLICY "extraction_costs_authenticated_all" ON public.extraction_costs
    FOR ALL TO authenticated
    USING     (org_id = public.current_org_id())
    WITH CHECK (org_id = public.current_org_id());

CREATE POLICY "extraction_costs_anon_deny" ON public.extraction_costs
    FOR ALL TO anon USING (false);
