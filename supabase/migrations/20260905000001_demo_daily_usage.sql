-- Migration: demo_daily_usage — global (cross-IP) daily cap on POST /demo/extract.
--
-- Why this exists: the 2026-09-05 state-reconstruction audit found /demo/extract has a
-- per-IP rate limit (5/min, in-process, not shared across Cloud Run's up to 3 replicas)
-- but NO cross-IP cap at all. That endpoint shares the SAME Groq API key -- and the SAME
-- free-tier daily token/request budget -- as every real paying customer's /v2/extract
-- call (app/core/config.py has exactly one groq_api_key). Groq's published free-tier
-- limits for openai/gpt-oss-20b and openai/gpt-oss-120b (both currently live, per
-- app/core/config.py) are 200,000 tokens/day and 1,000 requests/day each, shared across
-- every model call this process key makes. Measured average tokens per real extraction
-- (from this branch's own eval run, grouped by language): en ~1833, hi ~1019,
-- hi-en ~1934 tokens/call. At those rates, as few as ~103-196 unauthenticated,
-- keyless /demo/extract calls in a single day could exhaust the ENTIRE shared daily
-- token budget -- after which real, paying customers' extraction calls degrade or fail
-- for the rest of that day. This is an AVAILABILITY risk, not a billing risk (there is
-- no bill on the free tier) -- see app/api/demo.py's cap-check for the actual number.
--
-- Design: a single global counter row per calendar date (UTC), incremented atomically
-- per real (non-cache-hit) demo extraction. Deliberately NOT per-IP (that's already
-- covered by slowapi) and deliberately NOT keyed by org (there is no org on this path)
-- -- this is a single shared budget across every demo caller, matching the actual shared
-- resource it protects (one Groq key's one daily quota).
--
-- Cheapest durable store that adds no new billable infra: this repo's existing Supabase
-- Postgres (already provisioned, already paid for -- $0 marginal cost for one more small
-- table). No new service, no new dependency.

CREATE TABLE IF NOT EXISTS public.demo_daily_usage (
    usage_date      DATE          PRIMARY KEY,
    request_count   INTEGER       NOT NULL DEFAULT 0 CHECK (request_count >= 0),
    tokens_in_total INTEGER       NOT NULL DEFAULT 0 CHECK (tokens_in_total >= 0),
    tokens_out_total INTEGER      NOT NULL DEFAULT 0 CHECK (tokens_out_total >= 0),
    updated_at      TIMESTAMPTZ   NOT NULL DEFAULT now()
);

-- No RLS: this table carries no tenant data at all (a single global counter, not
-- per-org), so there is nothing for row-level security to scope. Locked down instead
-- by grants: review_iq_app (the app's own runtime connection role -- see
-- 20260726000001_review_iq_app_role.sql) gets exactly what it needs; anon and
-- authenticated (the Supabase-JWT-facing roles, never used by this backend-only table)
-- get nothing at all.
REVOKE ALL ON public.demo_daily_usage FROM PUBLIC;
REVOKE ALL ON public.demo_daily_usage FROM anon;
REVOKE ALL ON public.demo_daily_usage FROM authenticated;
GRANT SELECT, INSERT, UPDATE ON public.demo_daily_usage TO review_iq_app;
-- No DELETE -- this table is append/update-only (one row per date, incremented in
-- place); nothing in the app ever needs to remove a day's counter row.
