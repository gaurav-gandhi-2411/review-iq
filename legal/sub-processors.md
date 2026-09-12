> **⚠️ REQUIRES LEGAL REVIEW.** This document was drafted by an AI coding assistant from the
> product's actual technical implementation and standard SaaS legal templates. It is a
> starting draft, not legal advice, and must be reviewed by qualified counsel before being
> relied upon or published to customers.

# Sub-processors

This is the standing list of third parties that process Customer/end-customer data on behalf of
Samidha Reviews ("the Service"), referenced from the [Privacy Policy](./privacy-policy.md) and
[DPA template](./dpa-template.md). We will update this list when a sub-processor is added,
removed, or changed. `[TODO: GG-DECISION — pick and state a change-notification mechanism, e.g.
"check this page" vs. an email/changelog subscription, before this becomes a binding
contractual commitment (see dpa-template.md §6).]`

**Last verified against the codebase:** 2026-09-12 (Session 13, resolving the Groq ZDR TODO and
auditing every LLM-provider call site for Batch API usage — see
[ADR 0028](../docs/architecture/adr/0028-groq-zdr-verification-and-batch-api-audit.md)).
Previously updated 2026-09-12 (Session 12, adding OpenRouter and independently verifying Groq's
data-use commitment against its own Services Agreement). Originally drafted 2026-07-31
(Wave 1 Section E).

---

## Groq, Inc.

| | |
|---|---|
| **Role** | Primary LLM inference provider — currently `openai/gpt-oss-20b` and `openai/gpt-oss-120b`, routed by review length/complexity (`app/core/pricing.py`; model names change over time as Groq's hosted model lineup changes — this table reflects what's configured as of this drafting, not a permanent commitment to these specific models). |
| **What it processes** | Review text submitted to `/v2/extract`, `/v2/ingest/csv`, `/demo/extract`, etc., **after** the PII-redaction pass (`app/core/sanitize.py`) strips detected emails, phone numbers, and credit card numbers. Groq receives this redacted text to generate structured extraction and authenticity signal output. |
| **Data use commitment — independently verified against Groq's own primary legal document** (2026-09-12, `console.groq.com/docs/legal/services-agreement` §4.2): *"Groq is not permitted to use Inputs or Outputs for training or fine-tuning any AI Model Services or other models, unless explicitly granted permission or instructed by Customer."* Groq additionally does not retain Inputs/Outputs beyond what's needed to serve the request, except temporary logs (up to 30 days) for reliability/abuse monitoring, with a self-serve Zero Data Retention opt-out available in Console Data Controls. **This Services Agreement applies to free-tier/self-serve API use** — it is accepted by "using the Cloud Services," no Order Form or paid plan required, and a Data Processing Addendum is automatically incorporated into it. This supersedes the earlier (2026-07-31) unverified TODO on this row. | |
| **Their own published documentation** | `https://console.groq.com/docs/legal/services-agreement` (Services Agreement, §4.2 quoted above), `https://console.groq.com/docs/your-data` (Your Data in GroqCloud), `https://console.groq.com/docs/legal/customer-data-processing-addendum` (DPA) — all fetched and read directly, 2026-09-12. |
| **Data location** | `[TODO: the specific data-center region(s) Groq uses to physically serve inference were not independently verified — only the contractual no-training/limited-retention commitment above is confirmed. Verify before publishing if region-specific transfer language is needed.]` |
| **Zero Data Retention for this account** | **Inference APIs ZDR: enabled** (2026-09-12, Console Data Controls — reported by the account holder; not independently verifiable via API, see below). Global ZDR (which would also disable Batch/fine-tuning storage) is not yet enabled — safe to enable at any time: an independent code audit (2026-09-12) confirmed this codebase never calls Groq's Batch or fine-tuning APIs anywhere, only `chat.completions.create` and `models.list`, so Global ZDR's Batch/fine-tuning shutoff has zero functional impact. **Verification method note:** Groq's API exposes no response header or endpoint that reports ZDR status (confirmed by inspecting a real, live API response's full header set and Groq's own current API-reference and your-data docs directly — a search result claiming an `x-zero-data-retention` header exists for Groq was found to have conflated xAI's API, a different provider, and was discarded). The only way to check or change this setting is the Console UI; this row is therefore stated as the account holder's report, not an independent API verification. See [ADR 0028](../docs/architecture/adr/0028-groq-zdr-verification-and-batch-api-audit.md). |

---

## OpenRouter, Inc.

| | |
|---|---|
| **Role** | Secondary/failover LLM inference provider — activates only when Groq is unavailable (`app/core/providers/secondary.py`). Currently configured model: `meta-llama/llama-3.3-70b-instruct`. |
| **What it processes** | The same PII-redacted review text as Groq, only on Groq failure. |
| **Data use commitment — mechanism, not a blanket trust of OpenRouter's own policy**: OpenRouter is an aggregator; its own no-train policy does not bind every upstream provider it can route to. Every request from this integration unconditionally includes `"provider": {"zdr": true}`, which restricts OpenRouter's routing to endpoints on its own live Zero-Data-Retention allowlist (`GET /api/v1/endpoints/zdr`) — a model with no ZDR-flagged endpoint is refused (HTTP 404) rather than silently routed to a non-ZDR endpoint. **Live-verified twice**: 2026-07-31 (3 real calls, each cross-checked against the live ZDR allowlist, all 3 matched) and 2026-09-12 (one further real end-to-end call through this exact integration, routed to provider "Parasail," independently confirmed present on the live ZDR allowlist for this model at call time). |
| **Their own published documentation** | `https://openrouter.ai/docs/guides/features/zdr` (Zero Data Retention), `https://openrouter.ai/api/v1/endpoints/zdr` (the live allowlist itself, machine-readable). |
| **Data location** | Depends on which upstream ZDR-listed provider serves a given request (verified examples: DeepInfra, AkashML, Parasail) — OpenRouter itself does not commit to a fixed region for a given model; `[TODO: REQUIRES-LEGAL-REVIEW if this variability is acceptable for the transfer-mechanism language in privacy-policy.md §4, given the specific serving provider cannot be predicted in advance.]` |

---

## Supabase

| | |
|---|---|
| **Role** | Database (Postgres) hosting and authentication (magic-link email signup, JWT verification) |
| **What it processes** | All persisted Customer/end-customer data: extraction output, authenticity audit records (hashed review text, not plaintext — see `SECURITY.md` §10), account/organization data, API key metadata (hashed), usage records. Isolated per organization via Postgres Row-Level Security — see `docs/data-ownership.md`. |
| **Data location (this project)** | Verified from this project's own operational configuration, not guessed: the production Postgres connection pooler host is `aws-0-ap-south-1.pooler.supabase.com` (`ops/runbooks/connection-modes.md`), i.e. AWS `ap-south-1` (Mumbai, India). |
| **Their own published documentation** | `https://supabase.com/privacy` (Privacy Policy), `https://supabase.com/security` (Security overview) — `[TODO: these URLs were not independently fetched/verified in this drafting session; confirm they are current and review Supabase's own DPA/sub-processor list before publishing this page.]` |

---

## Google Cloud Run (our own infrastructure — listed for transparency)

| | |
|---|---|
| **Role** | Compute hosting for the Samidha Reviews API itself (`asia-south1` / Mumbai region — `ops/runbooks/cloud-run-deploy.md`). |
| **Why this is listed but treated differently from Groq/Supabase** | Google Cloud Run does not independently process Customer/end-customer data *on Customer's behalf* the way Groq (LLM inference) and Supabase (database) do — it is the compute substrate we, the Processor, run our own application code on. Under GDPR's sub-processor framework, this is closer to "Processor's own infrastructure" than a distinct sub-processor performing a discrete processing activity for Controller. We list it here anyway, in the interest of transparency, because review text and derived data do transit through and are held in memory on Cloud Run instances during request processing. |
| **What it processes** | Review text in-flight during API request handling (not persisted by Cloud Run itself — persistence happens in Supabase); secrets accessed via Google Cloud Secret Manager, scoped per-secret to the Cloud Run service account. |
| **Their own published documentation** | `https://cloud.google.com/terms/cloud-privacy-notice` (Google Cloud Privacy Notice), `https://cloud.google.com/security` — `[TODO: not independently verified in this drafting session.]` |

---

## Not currently in use

- **Google Gemini** — explicitly excluded from the customer-data (`/v2`) path. `SECURITY.md` §3:
  `allow_gemini_fallback=False` is hardcoded on every `/v2/extract` call; Gemini is reachable only
  on the legacy `/v1` demo path, gated behind `ENABLE_GEMINI_FALLBACK` (default `false`), because
  the Gemini free tier uses inputs for training and is therefore unsuitable for Customer review
  data. Listed here explicitly so it is clear this was a deliberate exclusion, not an oversight.
- **Payment processor** — none integrated as of this drafting (no billing exists yet — see
  `plan.md`'s Wave 2 scope). This list will be updated when one is added.

---

## A note on verification limitations, updated 2026-09-12

**Resolved since the original 2026-07-31 drafting**: Groq's data-use commitment is now
independently verified against its own primary legal document (Services Agreement §4.2, fetched
and read directly — see the Groq row above), and OpenRouter's ZDR enforcement has been
live-verified twice, most recently via a real end-to-end call through this exact integration.

**Still open, before this document is published to customers:**

1. Supabase's and Google Cloud's own privacy/security URLs (canonical, well-known, but not
   independently re-fetched this session) — confirm current and pull specific data-residency
   statements.
2. Groq's physical data-center region(s) for inference specifically (the no-training/limited-
   retention *commitment* is confirmed; *where* the compute runs is not).
3. Whether Groq's *Global* Zero Data Retention setting gets enabled (Inference-APIs ZDR is
   already enabled per the account holder, 2026-09-12 — see the Groq row above; Global ZDR is
   confirmed safe to enable, pending the account holder's action, since this codebase never uses
   Groq's Batch or fine-tuning APIs). Note this project has no way to independently re-verify
   *either* ZDR scope via the API going forward — Groq exposes no header or endpoint for this
   (see [ADR 0028](../docs/architecture/adr/0028-groq-zdr-verification-and-batch-api-audit.md)) —
   so this row will always be the account holder's report, not an automated check.
4. Whether either Groq or Supabase has published its own DPA that review-iq should reference or
   attach.

---

_This document is version-controlled; see git history for changes. Originally drafted 2026-07-31;
updated 2026-09-12 (OpenRouter added, Groq facts independently verified)._
