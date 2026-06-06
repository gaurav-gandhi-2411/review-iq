# Security

This document describes the security controls in review-iq as of Phase 2.0a / v0.2.0+.

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

**Primary provider:** Groq (Llama 3.3 70B). Groq's API terms state that API customer inputs are not used for model training.

**Gemini (Google Gemini 2.0 Flash):** Removed from the v2 (client-data) path entirely. `allow_gemini_fallback=False` is hardcoded on every `/v2/extract` call. Gemini is reachable only on the legacy `/v1` demo path, and only when `ENABLE_GEMINI_FALLBACK=true` is explicitly set (default: `false`). This restriction exists because the Gemini free tier uses inputs for training and is therefore unsuitable for client review data.

All review text is PII-redacted before being sent to any provider.

---

## 4. Tenant Isolation

**Database (Postgres via Supabase):** Row-Level Security (RLS) is enabled on all tenant tables (`organizations`, `api_keys`, `extractions`, `usage_records`). Every query sets `SET LOCAL "app.current_org_id"` so a miscoded query from organization A cannot return rows belonging to organization B.

**API keys:** Keys follow the format `riq_live_<32 hex chars>`. The plaintext key is shown once at creation and never written to disk or logs. Keys are stored as argon2id hashes. The key prefix (first 17 characters) is indexed for O(1) lookup without exposing the full hash.

**Quota enforcement:** Monthly extraction quotas are enforced with a `SELECT FOR UPDATE` lock, preventing TOCTOU races under concurrent requests.

---

## 5. Secret Management

All secrets (`GROQ_API_KEY`, `ADMIN_PASSWORD_HASH`, `SUPABASE_DATABASE_URL`, `GEMINI_API_KEY`) are stored in Google Cloud Secret Manager. The Cloud Run service account is granted `secretAccessor` on a per-secret basis — not project-wide.

No plaintext secrets exist in source code or committed environment files. `.env` is gitignored. `.env.example` contains only placeholder values.

---

## 6. Responsible Disclosure

If you discover a security vulnerability, please report it privately before opening a public issue.

- **Email:** gaurav.gandhi2411@gmail.com
- **GitHub:** https://github.com/gaurav-gandhi-2411/review-iq/issues (label: `security`)
- **Expected response time:** 72 hours

Please include a description of the issue, reproduction steps, and any relevant logs or screenshots. We will acknowledge receipt within 72 hours and aim to ship a fix or mitigation within 14 days for critical issues.

---

_This document covers Phase 2.0a / v0.2.0+ behavior. Controls may change as the product evolves; check git history for changes to this file._
