> **⚠️ REQUIRES LEGAL REVIEW.** This document was drafted by an AI coding assistant from the
> product's actual technical implementation and standard SaaS legal templates. It is a
> starting draft, not legal advice, and must be reviewed by qualified counsel before being
> relied upon or published to customers.

# Data Processing Agreement (Template)

This Data Processing Agreement ("DPA") forms part of the Terms of Service between **Gaurav
Gandhi, operating Samidha Reviews** ("Processor", "we") and the business customer identified at
signup ("Controller", "you", "Customer"), governing Processor's processing of personal data on
Controller's behalf in connection with the Service.

`[TODO: GG/counsel — this is a template to be executed (referenced, countersigned, or
incorporated by reference into the Terms) per customer/contract as needed, not a
self-executing document.]`

---

## 1. Subject matter and duration

**Subject matter:** Processor's provision of the Samidha Reviews API — review text extraction,
authenticity scoring, and aggregate insights — on behalf of Controller.

**Duration:** This DPA is effective for as long as Processor processes personal data on
Controller's behalf under the Terms of Service, and terminates automatically upon the earlier of
(a) termination of the underlying Terms, or (b) completion of the deletion process described in
§8 (Return/deletion of data) below.

---

## 2. Nature and purpose of processing

| | |
|---|---|
| **Nature of processing** | Automated extraction (LLM-based structured data extraction), authenticity scoring, storage, and aggregate insight generation. See §"Processing operations" below for the specific operations. |
| **Purpose of processing** | To provide the Service Controller has signed up for: turning Controller-submitted review text into structured, queryable data and authenticity signals. |
| **Processing operations** | (1) PII redaction pass on submitted review text; (2) transmission of redacted text to the LLM sub-processor (Groq) for extraction; (3) storage of extraction output, hashed authenticity audit records, and usage records in the database sub-processor (Supabase Postgres); (4) return of structured output to Controller via the API. |

---

## 3. Categories of data subjects and personal data

**Data subjects:** the end-customers of Controller who authored the review text Controller
submits to the Service (i.e. Controller's own shoppers/customers) — **not** Processor's direct
relationship, since Processor has no direct relationship with these individuals.

**Categories of personal data (as they may appear within submitted review text):** names, email
addresses, phone numbers, order/invoice IDs, and any other personal data an end-customer chose to
include in free-text review content. Processor does not request or require any specific category
of personal data — whatever appears in the review text Controller submits is what is processed.

**Special category data:** the Service is not designed to process special category / sensitive
personal data (health, biometric, etc.). Controller is responsible for not submitting review text
containing special category data, and for ensuring any such data that inadvertently appears is
handled per applicable law. `[TODO: REQUIRES-LEGAL-REVIEW — whether an explicit contractual
prohibition on submitting special-category data is needed.]`

---

## 4. Controller obligations

Controller warrants that:

1. It has a lawful basis (e.g. consent, legitimate interest, or contract) to collect the review
   text it submits, including any personal data embedded in it, under applicable data protection
   law (GDPR, DPDP Act 2023, or other law that applies to Controller's own end-customers).
2. It has provided any notices to its end-customers required by applicable law regarding
   processing of their review data, including that reviews may be processed by automated /
   AI-based tooling.
3. It is responsible for responding to its own end-customers' data subject rights requests
   (access, deletion, correction) concerning the underlying review data, with Processor's
   reasonable assistance as described in §6.
4. It will not submit review text it is not lawfully entitled to submit for processing.

---

## 5. Processor obligations

Processor shall:

1. Process personal data only on documented instructions from Controller (i.e. to provide the
   Service as configured/used by Controller), unless required to do otherwise by law.
2. Ensure persons authorized to process the data are subject to confidentiality obligations.
3. Implement appropriate technical and organizational security measures — see §7 below for what
   is actually implemented today.
4. Not engage a sub-processor without authorization — see §6 (Sub-processors), which names the
   currently-authorized sub-processors.
5. Assist Controller, taking into account the nature of processing, in responding to data subject
   rights requests, to the extent the Service enables this — concretely, Controller can retrieve
   its full stored dataset via `GET /v2/dataset/export?format=jsonl` and delete it entirely via
   the account-deletion flow (§8), which are the mechanisms available to fulfil access/deletion
   requests today.
6. Assist Controller with data protection impact assessments and prior consultations with
   supervisory authorities where required, to the extent reasonably requested.
7. Notify Controller of a personal data breach without undue delay — see §9 (Breach
   notification).
8. Make available to Controller information necessary to demonstrate compliance with this DPA,
   and allow for and contribute to audits — see §10 (Audit rights).

---

## 6. Sub-processor authorization

Controller provides general authorization for Processor to engage the following sub-processors,
each of which processes personal data within submitted review text (or, for Supabase, personal
data more broadly) on Controller's behalf:

| Sub-processor | Role | What it processes |
|---|---|---|
| **Groq, Inc.** | LLM inference (primary provider — Llama 3.3 70B / 3.1 8B) | Redacted review text, for the sole purpose of generating structured extraction and authenticity signal output. Not used for model training per Groq's API terms (see [`sub-processors.md`](./sub-processors.md)). |
| **Supabase (Supabase, Inc. / Supabase Pte. Ltd.)** | Database (Postgres) and authentication hosting | All persisted Customer/end-customer data: extraction output, hashed authenticity audit records, account data, usage records — isolated per organization via Row-Level Security. |

See [`sub-processors.md`](./sub-processors.md) for the complete, currently-maintained list
(including infrastructure providers like Google Cloud Run, listed there for transparency though
not treated as a sub-processor of Controller data in the strict GDPR sense).

Processor will provide notice of any new sub-processor before it begins processing Controller
data where practicable, allowing Controller a reasonable objection period.
`[TODO: REQUIRES-LEGAL-REVIEW / GG-DECISION — a specific mechanism (e.g. a subscribable
changelog on sub-processors.md, or a direct email notice) should be chosen before this is a firm
contractual commitment.]`

---

## 7. Security measures

The following technical and organizational measures are **currently implemented**, verified
against this repository's `SECURITY.md` and `docs/data-ownership.md` at the time of drafting —
this DPA describes real, shipped controls, not aspirational ones:

- **PII redaction before egress** — review text is passed through a sanitizer before being sent
  to any LLM sub-processor, redacting detected emails, phone numbers, and credit card numbers
  (`app/core/sanitize.py`). This is currently a destructive redaction, not a reversible token
  map (see the [Privacy Policy](./privacy-policy.md) §2 for the distinction).
- **Multi-tenant database isolation** — Postgres Row-Level Security (RLS) policies on every
  tenant table, enforcing both `USING` (read) and `WITH CHECK` (write) org-scoping, plus an
  explicit anon-deny policy. The application connects via a dedicated, non-superuser database
  role (`review_iq_app`). See `docs/data-ownership.md` for the full isolation model and its
  verification tests.
- **Encryption in transit** — API traffic is served over HTTPS (Google Cloud Run TLS
  termination); database connections to Supabase Postgres use TLS per Supabase's standard
  connection requirements.
- **Credential hashing** — API keys are stored as argon2id hashes, never in plaintext, and shown
  to the Customer only once at creation/regeneration.
- **Secret management** — provider credentials (`GROQ_API_KEY`, `SUPABASE_SERVICE_ROLE_KEY`,
  etc.) are held in Google Cloud Secret Manager with per-secret, least-privilege access grants —
  not in source code or committed environment files.
- **Prompt injection defenses** — a two-layer defense (pre-LLM regex filter + hardened system
  prompt) reduces the risk of submitted review text manipulating the LLM beyond its intended
  extraction task.
- **Audit trail** — authenticity scoring decisions are recorded in an org-scoped
  `authenticity_audits` table (hashed review text, not plaintext), itself subject to the same RLS
  isolation.

**Not yet implemented** (do not represent these as current controls until they ship):
reversible/rehydratable PII tokenization; a formal SOC 2 or ISO 27001 certification for Processor
itself (as opposed to relying on sub-processors' own certifications — see
[`sub-processors.md`](./sub-processors.md)); a dedicated customer-facing audit-log export (listed
as a Wave 1 work item, not yet shipped).

---

## 8. Return / deletion of data

Controller may retrieve its full stored dataset at any time via
`GET /v2/dataset/export?format=jsonl` and permanently delete its account and all associated data
via the self-service `DELETE /account` flow. See
[`data-retention-and-deletion.md`](./data-retention-and-deletion.md) for the exact mechanism,
including the honest disclosure that encrypted database backups may retain a copy of deleted data
for up to the current backup-retention ceiling before that backup itself expires.

Upon termination of the underlying Terms, Processor will delete Controller's personal data in
accordance with the retention schedule in that document, unless retention is required by
applicable law.

---

## 9. Breach notification

`[TODO: REQUIRES-LEGAL-REVIEW — the timeframe below is drafted using GDPR Art. 33's 72-hour
notification-to-supervisory-authority standard as the reference point for a
processor-to-controller commitment, which is a reasonable template default but must be confirmed
by counsel, particularly against DPDP Act 2023's own (currently less prescriptive) breach-notice
expectations.]`

Processor will notify Controller without undue delay, and in any event within **72 hours** of
becoming aware of a personal data breach affecting Controller's data, providing (to the extent
known at the time): the nature of the breach, categories and approximate number of data subjects
and records affected, likely consequences, and measures taken or proposed to address it. This
notification obligation is independent of, and does not replace, Controller's own obligation to
notify supervisory authorities or affected data subjects where required by law.

---

## 10. Audit rights

Controller may request evidence of Processor's compliance with this DPA (e.g. this repository's
`SECURITY.md`, `docs/data-ownership.md`, and the RLS isolation test suite results) no more than
once per 12-month period, or following a security incident affecting Controller's data, on
reasonable notice. `[TODO: REQUIRES-LEGAL-REVIEW / GG-DECISION — whether to offer on-site/
third-party audits, and at whose cost, is a business decision that should be set before this
becomes a binding term, especially for a solo-operated Service where a full third-party audit
program does not yet exist.]`

---

## 11. International transfer

See the [Privacy Policy](./privacy-policy.md) §4 and [`sub-processors.md`](./sub-processors.md)
for the current, factually-grounded transfer footprint (Supabase database hosted in AWS
`ap-south-1`/Mumbai for this project; Groq's data-center locations not independently verified in
this drafting session; nightly encrypted backups transiting through US-hosted GitHub Actions
storage). `[TODO: REQUIRES-LEGAL-REVIEW — appropriate transfer mechanism (SCCs or equivalent) for
any legs outside Controller's/data subjects' jurisdiction.]`

---

_This is a template DPA drafted from the product's actual technical implementation. It is
version-controlled; see git history for changes. Last drafted: 2026-07-31._
