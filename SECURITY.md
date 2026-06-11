# Security

This document describes the security controls in review-iq as of Phase 2.0c / v0.4.0+.

---

## 1. PII Redaction

All review text is sanitized before being forwarded to any LLM provider (`app/core/sanitize.py`).
The sanitizer redacts the following patterns, replacing them with `[REDACTED]` placeholders:

- Email addresses
- Phone numbers — Indian formats (10-digit, +91 prefix) and common international formats
- Credit card numbers (major card patterns)

Sanitization runs unconditionally on every extraction request; it cannot be bypassed by callers.

---

## 2. Prompt Injection Defense

Two independent layers guard against prompt injection attacks.

**Layer 1 — Pre-LLM regex filter (`sanitize.py`):** Common injection phrases (e.g. "ignore previous instructions", "you are now") are detected via regex before the text reaches the model. A boolean `is_suspicious` flag is logged on every request for monitoring purposes.

**Layer 2 — Hardened system prompt (`llm.py`):** The LLM system prompt explicitly marks content inside `<review>` tags as untrusted user data and instructs the model never to obey directives embedded within the review text. The model is told its only task is structured extraction.

---

## 3. LLM Data Handling

**Primary provider:** Groq (Llama 3.3 70B and Llama 3.1 8B). Groq's API terms state that API customer inputs are not used for model training. Both the large and small Groq models used in tiered routing share this guarantee.

**Secondary failover provider:** A configurable secondary provider can be wired via `SECONDARY_PROVIDER_API_KEY` / `SECONDARY_PROVIDER_MODEL`. The code enforces a data-handling check at the call site via `assert_privacy_safe()` — any provider whose `trains_on_input` property is `True` raises `PrivacyViolation` before the prompt is sent, making it impossible to accidentally route client data to a training-on-input provider on the org-key path. This check is unconditional; it cannot be bypassed by configuration.

**Gemini (Google Gemini 2.0 Flash):** Removed from the v2 (client-data) path entirely. `allow_gemini_fallback=False` is hardcoded on every `/v2/extract` call. Gemini is reachable only on the legacy `/v1` demo path, and only when `ENABLE_GEMINI_FALLBACK=true` is explicitly set (default: `false`). This restriction exists because the Gemini free tier uses inputs for training and is therefore unsuitable for client review data.

All review text is PII-redacted before being sent to any provider.

---

## 4. Tenant Isolation

**Database (Postgres via Supabase):** Row-Level Security (RLS) is enabled on all tenant tables (`organizations`, `api_keys`, `extractions`, `usage_records`, `batch_jobs`). Every query sets `SET LOCAL "app.current_org_id"` so a miscoded query from organization A cannot return rows belonging to organization B.

**API keys:** Keys follow the format `riq_live_<32 hex chars>`. The plaintext key is shown once at creation (and once again only on explicit regenerate) and never written to disk or logs. Keys are stored as argon2id hashes. The key prefix (first 17 characters) is indexed for O(1) lookup without exposing the full hash.

**Quota enforcement:** Monthly extraction quotas are enforced with a `SELECT FOR UPDATE` lock, preventing TOCTOU races under concurrent requests. The free-tier hard caps (100 extractions/month, 500 rows/upload, 5 MB/file) are enforced server-side and cannot be bypassed by callers.

---

## 5. Self-Serve Auth (Supabase JWT path)

The signup and account endpoints (`/auth/provision`, `GET /account`, `POST /account/regenerate-key`) use a separate auth channel: a Supabase JWT (magic-link email flow), not the `riq_live_*` API key.

JWT verification calls `supabase.auth.get_user(jwt)` via the Supabase SDK; the SDK validates the token signature against Supabase's JWKS. No JWT decoding is done in-process. A 401 is returned on any verification failure.

The `_provision_org_and_key` path creates exactly one org, one owner membership, and one API key per user. Re-calling provision with an existing user returns `{"status": "existing"}` — the raw_key is never re-exposed; callers must use `POST /account/regenerate-key` to cycle the key (which again shows it once).

---

## 6. CSV Ingest Security

All PII redaction and prompt injection defenses (sections 1–2) apply per-row to every CSV upload. Rows are sanitized before any LLM call.

Streaming parse rejects uploads exceeding 5 MB before fully loading them into memory. The row cap (500) is enforced before any extraction begins. Input file content is never persisted to disk — only the extracted JSON is stored.

---

## 7. Demo Endpoint

`POST /demo/extract` requires no API key and performs no database writes. PII redaction and prompt injection defenses still apply. The endpoint is rate-limited globally (30 requests/minute across all callers) via slowapi. No review text is stored or logged beyond the standard structured log line.

---

## 8. Secret Management

All secrets (`GROQ_API_KEY`, `ADMIN_PASSWORD_HASH`, `SUPABASE_DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `GEMINI_API_KEY`) are stored in Google Cloud Secret Manager. The Cloud Run service account is granted `secretAccessor` on a per-secret basis — not project-wide.

No plaintext secrets exist in source code or committed environment files. `.env` is gitignored. `.env.example` contains only placeholder values.

---

## 10. Authenticity Audit Trail

The `authenticity_audits` table is subject to the same RLS tenant-isolation as `extractions` and `batch_jobs`. Each row stores `org_id`, `review_hash` (SHA-256 of the review text, not the plaintext), `score`, `label`, and `flags`. Plaintext review content is never persisted in the audit table.

Row-Level Security policies enforce that:
- Authenticated requests can only read and insert rows where `org_id = public.current_org_id()` (the `WITH CHECK` clause prevents cross-tenant insertion).
- Anonymous requests are denied unconditionally.

Cross-tenant isolation is verified by an integration test (`tests/integration/test_authenticity_isolation.py`) that asserts org A cannot read org B's audit rows and cannot insert a row with org B's `org_id`.

**IS 19000:2022 compliance posture:** The authenticity scorer *supports / assists* human review-administrator moderation workflows. It flags reviews for human decision — it does not auto-reject reviews, and it does not certify or guarantee IS 19000 compliance. See `docs/compliance.md`.

---

## 9. Responsible Disclosure

If you discover a security vulnerability, please report it privately before opening a public issue.

- **Email:** gaurav.gandhi2411@gmail.com
- **GitHub:** https://github.com/gaurav-gandhi-2411/review-iq/issues (label: `security`)
- **Expected response time:** 72 hours

Please include a description of the issue, reproduction steps, and any relevant logs or screenshots. We will acknowledge receipt within 72 hours and aim to ship a fix or mitigation within 14 days for critical issues.

---

_This document covers Phase 2.2 / v0.6.0+ behavior. Controls may change as the product evolves; check git history for changes to this file._
