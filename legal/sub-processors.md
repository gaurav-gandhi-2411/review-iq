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

**Last verified against the codebase:** 2026-08-01 (added Resend, absent from the original Section
E drafting despite being live in production already).

---

## Groq, Inc.

| | |
|---|---|
| **Role** | Primary LLM inference provider — Llama 3.3 70B and Llama 3.1 8B models |
| **What it processes** | Review text submitted to `/v2/extract`, `/v2/ingest/csv`, `/demo/extract`, etc., **after** the PII-redaction pass (`app/core/sanitize.py`) strips detected emails, phone numbers, and credit card numbers. Groq receives this redacted text to generate structured extraction and authenticity signal output. |
| **Data use commitment (as implemented in this codebase)** | Per `SECURITY.md` §3: "Groq's API terms state that API customer inputs are not used for model training." This is enforced in code via `assert_privacy_safe()` (`app/core/llm.py`), which raises `PrivacyViolation` before any prompt is sent to a provider whose `trains_on_input` property is `True` — this check is unconditional on the org-key (`/v2`) path. |
| **Their own published documentation** | `https://groq.com/privacy-policy/` (Groq's Privacy Policy) — `[TODO: this URL and any linked DPA/trust-center page were not independently fetched/verified in this drafting session (no live web-fetch tool was available to this agent). Verify the URL is current and review Groq's DPA/data-processing terms before publishing this page or executing any DPA that names Groq.]` |
| **Data location** | `[TODO: not verified in this session — see privacy-policy.md §4. Confirm with Groq's current documentation before publishing.]` |

---

## Supabase

| | |
|---|---|
| **Role** | Database (Postgres) hosting and authentication (magic-link email signup, JWT verification) |
| **What it processes** | All persisted Customer/end-customer data: extraction output, authenticity audit records (hashed review text, not plaintext — see `SECURITY.md` §10), account/organization data, API key metadata (hashed), usage records. Isolated per organization via Postgres Row-Level Security — see `docs/data-ownership.md`. |
| **Data location (this project)** | Verified from this project's own operational configuration, not guessed: the production Postgres connection pooler host is `aws-0-ap-south-1.pooler.supabase.com` (`ops/runbooks/connection-modes.md`), i.e. AWS `ap-south-1` (Mumbai, India). |
| **Their own published documentation** | `https://supabase.com/privacy` (Privacy Policy), `https://supabase.com/security` (Security overview) — `[TODO: these URLs were not independently fetched/verified in this drafting session; confirm they are current and review Supabase's own DPA/sub-processor list before publishing this page.]` |

---

## Resend

| | |
|---|---|
| **Role** | Transactional alert email delivery (quota-warning, authenticity, and urgency notifications — `app/core/alerts/channels/resend_channel.py`). **Not** used for magic-link signup email, which Supabase's own default sender handles (verified live 2026-07-31 — a real signup email arrived from `noreply@mail.app.supabase.io`, not Resend). |
| **What it processes** | Alert content derived from Customer data — e.g. an urgency-flagged review's summary, or a quota-usage figure — sent to the org's own registered recipient email. Added to this list 2026-08-01; previously absent despite being live in production since before this drafting. |
| **Data location** | `[UNVERIFIED — retracted 2026-08-01: an earlier drafting pass asserted "Tokyo, Japan (ap-northeast-1), confirmed from Resend's own dashboard" with no screenshot, export, or API response ever actually produced to support it — no artifact backing this claim exists in this session. The existing send-only Resend API key cannot read account/region settings (confirmed live: querying its api-keys endpoint returns 401, "restricted to only send emails"). GG: confirm the region shown at resend.com under Domains for mail.samidhareviews.xyz, or under Settings, and report back before this line is filled in.]` |
| **Their own published documentation** | `https://resend.com/legal/privacy-policy` — `[TODO: not independently fetched/verified in this drafting session; confirm current before publishing.]` |

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

## A note on verification limitations in this drafting session

The URLs above for Groq's and Supabase's own privacy/security documentation are the canonical,
well-known URLs for each company as general public knowledge, but this drafting session had no
live web-fetch tool available to confirm they resolve correctly today, that they are the current
version, or to extract their stated data-center regions for the LLM inference leg (Groq)
specifically. **Before this document is published to customers, someone with live web access
must:** (1) confirm both URLs are correct and current, (2) pull Groq's and Supabase's specific
data-residency and sub-processor statements to fill the `[TODO]` blocks above, and (3) confirm
whether either company has published its own DPA that review-iq should reference or attach.

---

_This document is version-controlled; see git history for changes. Last drafted: 2026-07-31._
