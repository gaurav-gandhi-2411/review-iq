# Review-IQ — Close-Out Roadmap

> **Definition of done for this project:** a multi-tenant hosted SaaS that a stranger in India can sign up for, push reviews to (English *and* Hinglish/Hindi), and pull structured insights from — with the open eval table as the sales pitch. SDKs, browser extension, and embed widget are **post-sale growth**, explicitly out of the close-out.

**Current state:** v0.1.3, single-tenant, SQLite, Groq→Gemini, live on HF Spaces. Phase 1 complete. 25 fixtures present on remote, eval-in-CI at 85% (last run 85.6%), 128 tests, mypy strict, 3 CI workflows.

---

## The cut line

| Phase | What ships | Sellable checkpoint |
|---|---|---|
| **2.0a** Multi-tenant SaaS on Cloud Run | API keys, per-key quotas, Postgres + RLS, usage metering, tenant isolation, kill-switch + budget caps, `/v2/*` endpoints | **First rupee.** Sellable to SMBs / DTC brands as a hosted English API. |
| **2.0b** India language moat | `lingua-py` detection, branched Hinglish/Hindi prompts, ~55 fixtures from real Flipkart data, public accuracy table | **The differentiated pitch.** The thing Yotpo/Birdeye can't match. |
| **2.0c** Ingestion + self-serve | CSV bulk upload + export, email→API-key sign-up, minimal docs + landing page | **A stranger can onboard themselves.** Close-out done. |
| — line — | *Everything below is growth, not close-out* | |
| 2.5 | Python + JS SDKs | reduces integration friction |
| 3.0 | Browser extension + embed widget | viral surface |
| 3.5 / 4.0 | Slack alerts, drift, webhooks, vector search | only on real client demand |

The project is **closeable and sellable at the end of 2.0c.** 2.0a alone is already revenue-capable for SMBs; 2.0b is what makes it defensible in the India market.

---

## Decisions I'm making as your product advisor (these resolve open questions in `plan.md`)

1. **Kill Gemini on the client-data path.** Gemini free tier trains on inputs — a hard blocker for client review data. Prod extraction is **Groq-only**; Gemini stays behind a `DEV_ONLY` flag, never invoked when a real org key is present. This is non-negotiable for sellability and goes into 2.0a, not later.
2. **Keep SQLite — don't rip it out.** `plan.md` says "drop SQLite, start clean on Postgres." Correct for *prod data*, wrong for the codebase. Introduce a `ReviewRepository` interface with two backends: SQLite (dev/test — keeps the 128 tests offline and fast) and Postgres (prod). One interface, two impls. This protects your test suite and your CI speed.
3. **Two-track go-to-market, one codebase.** Small vendors → self-serve hosted multi-tenant. Flipkart/Amazon-scale enterprise → the same OSS, self-hosted on their own infra (isolation/SLA/DPA become a *sales + legal* conversation, not code). The MIT-OSS posture is your enterprise wedge: they can run it air-gapped. Don't build enterprise SSO/SLA tooling speculatively.
4. **Ingestion is a first-class sellable feature, not a footnote.** Small Indian vendors live in spreadsheets and marketplace CSV exports. CSV-in / CSV-out (2.0c) matters more for SMB conversion than SDKs. I've pulled it forward, ahead of the SDK work.
5. **`review-iq-prod` is approval-gated.** Per your standing instruction, every `gcloud` action against `review-iq-prod` is an explicit per-action escalation to you. The kill-switch + budget caps ship **before** any production traffic — encoded as a hard rule in the 2.0a spec.

---

## Phase 2.0a — Multi-tenant SaaS (the spec.md attached covers this in full)

Build order: GCP project + cost controls + kill-switch **first** → Supabase Postgres + RLS → tenancy schema (orgs/users/members/api_keys/usage_records, `org_id` on extractions) → API-key auth middleware (`riq_live_<hex>`, hashed at rest, quota + usage recording) → tenancy CRUD + owner-only admin endpoints → `/v2/*` tenant-scoped endpoints (v1 untouched) → repository abstraction (SQLite/Postgres) → Cloud Run deploy + Secret Manager → isolation + quota tests → eval re-run → `v0.2.0`.

**Done when:** cross-tenant isolation proven (org A can't read org B), quota enforcement returns 429, eval ≥85% on Cloud Run, $0 spend confirmed, kill-switch tested via simulated breach.

---

## Phase 2.0b — India language moat (next spec, after 2.0a is green)

- `lingua-py` language detection before the LLM call; `language` field already exists in the schema.
- Branched prompt templates per language (same schema, localized few-shot examples). Llama 3.3 70B handles Hinglish natively — no translation layer.
- Real-data sourcing script (`eval/data/sample.py`) pulling candidates from Flipkart Reviews (Kaggle, CC0) + Amazon Reviews 2023; CC pre-filters ~100 candidates, **you hand-label ~30 in a ~2hr session**.
- Eval expands to ~55 fixtures: 25 English (untouched) + 18 Hinglish + 6 Hindi + (6 Tamil optional). Accuracy table per language goes in the README — that table *is* the marketing.
- `v0.2.1`.

**Done when:** ≥85% accuracy on each language bucket, public per-language accuracy table, `language` correctly populated end-to-end.

---

## Phase 2.0c — Ingestion + self-serve (final close-out spec)

- **CSV bulk ingestion:** upload a marketplace export → batch extract → poll job → download structured CSV/JSON. Reuses the existing batch pipeline + `BatchJob` model.
- **Self-serve onboarding:** email → org created → `riq_live_` key issued (Supabase Auth handles the email side). Free tier: 100 extractions/mo, enforced via existing quota machinery.
- **Minimal landing + docs:** Cloudflare Pages — hero, live demo, the accuracy table, curl/Python quick-start, "self-host or hosted," transparent "how we make money." `mkdocs` or plain HTML for `/docs`.
- `v0.3.0` → **project closed as a sellable product.**

**Done when:** a stranger goes from landing page → working API key → first extraction in under 10 minutes, with no manual step from you.

---

## Out of close-out (growth backlog, sequence later only if demand appears)

SDKs (PyPI + npm) · browser extension (Firefox AMO) · embed widget · Slack alerts · drift detection · weekly digest · webhook ingestion · vector search · multi-region. None of these block "sellable." Each becomes its own spec.md if a real client asks.

---

## Things that are NOT engineering and stay with you

Pricing page numbers · Stripe (invoice manually for first clients) · ToS / Privacy Policy / DPA (need real templates at first client) · the first DTC-brand demo conversation. Flagged here so they don't silently become "done" criteria the orchestrator can't satisfy.
