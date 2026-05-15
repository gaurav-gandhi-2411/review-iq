---
title: Review IQ
emoji: 🔍
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# review-iq

Open-source API that turns unstructured customer reviews into structured JSON — sentiment, topics, pros/cons, competitor mentions, urgency signals. Every prompt, eval fixture, and accuracy number is public and version-controlled. English, Hinglish, and Hindi are all supported as of v0.3.0.

[![CI](https://github.com/gaurav-gandhi-2411/review-iq/actions/workflows/ci.yml/badge.svg)](https://github.com/gaurav-gandhi-2411/review-iq/actions/workflows/ci.yml)
![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.11-blue)

**Production-grade review intelligence service. Unstructured customer reviews → queryable structured insights.**

> Turn rambling customer feedback into clean, queryable JSON — with sentiment, topics, competitor mentions, urgency signals, and more.

---

## I just want to see it work

No setup. Hit the live API with the public demo key (100 calls/month, shared):

```bash
curl -s -X POST https://review-iq-ajjrytb3na-el.a.run.app/v2/extract \
  -H "X-API-Key: riq_live_d6fb4d0cb8a942231e4128cea7562ce2" \
  -H "Content-Type: application/json" \
  -d '{"text": "Battery dies in 20 min. Suction is excellent but $200 feels overpriced. Would not buy again."}'
```

Quota exhausted or want your own key? Drop a message to gaurav-gandhi-2411 on GitHub or LinkedIn — self-serve sign-up is on the Phase 2.5 roadmap.

---

## Why this exists

Most review analytics tools are black boxes: you get a score with no methodology. Review-IQ takes the opposite approach — every prompt is in the repo, every eval fixture is versioned, and CI breaks if accuracy drops below 85%. Built with Indian DTC brands in mind: Phase 2 adds native Hinglish and Hindi support that incumbents (Yotpo, Birdeye, Trustpilot Insights) don't offer. Fully MIT — same code self-hosters run is what the hosted version runs; no feature gates.

---

## Live endpoints

| Version | URL | Notes |
|---|---|---|
| **v2 — production** | `https://review-iq-ajjrytb3na-el.a.run.app` | Cloud Run · multi-tenant · argon2id API keys · Postgres/RLS |
| v1 — legacy demo | `https://gauravgandhi2411-review-iq.hf.space` | HF Spaces · single-tenant · SQLite · no auth required |

v1 remains live for demo purposes. All new integrations should target v2.

---

## Quick start

### v2 (production — Cloud Run)

Obtain an API key from your org admin via the `/admin` endpoints, then:

```bash
# Extract structured insights from a review
curl -X POST https://review-iq-ajjrytb3na-el.a.run.app/v2/extract \
  -H "X-API-Key: $REVIEW_IQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "The Turbo-Vac 5000 has incredible suction but the battery dies in 15 minutes. For $300 I expected better. Would buy a Dyson next time."}'
```

```json
{
  "product": "Turbo-Vac 5000",
  "stars": null,
  "stars_inferred": 3,
  "pros": ["incredible suction"],
  "cons": ["poor battery life (15 minutes)", "poor build quality for price"],
  "buy_again": false,
  "sentiment": "mixed",
  "topics": ["suction", "battery", "build_quality", "price"],
  "competitor_mentions": ["Dyson"],
  "urgency": "low",
  "feature_requests": [],
  "language": "en",
  "extraction_meta": {
    "model": "llama-3.3-70b-versatile",
    "prompt_version": "v2.0",
    "schema_version": "1.0.0",
    "latency_ms": 820
  }
}
```

```bash
# Batch — up to 100 reviews
curl -X POST https://review-iq-ajjrytb3na-el.a.run.app/v2/extract/batch \
  -H "X-API-Key: $REVIEW_IQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"reviews": [{"text": "Great product!"}, {"text": "Terrible quality, returned."}]}'

# Query stored extractions for your org
curl "https://review-iq-ajjrytb3na-el.a.run.app/v2/reviews?sentiment=negative&urgency=high" \
  -H "X-API-Key: $REVIEW_IQ_API_KEY"

# Aggregated insights
curl https://review-iq-ajjrytb3na-el.a.run.app/v2/insights \
  -H "X-API-Key: $REVIEW_IQ_API_KEY"
```

### v1 (legacy demo — HF Spaces)

```bash
curl -X POST https://gauravgandhi2411-review-iq.hf.space/extract \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "Great product, fast shipping!"}'
```

---

## Eval results

Evaluated on 46 hand-labeled and synthetic fixtures across English, Hinglish, and Hindi:

| Version | Environment | Overall | en | hi-en | hi | Fixtures |
|---|---|---|---|---|---|---|
| v0.3.0 | CI / direct LLM | **TBD** | TBD | TBD | TBD | 46 (25 en + 15 hi-en + 6 hi) |
| v0.2.0 | Cloud Run (production) | **87.9%** | 87.9% | — | — | 25 |
| v0.1.3 | HF Spaces | 86.7% | 86.7% | — | — | 25 |

Gates: overall ≥ 85%, per-language ≥ 80%. Eval runs automatically in CI on every push touching prompts, LLM, schema, or fixture files. Nightly runs post results to Slack.

---

## API reference

### v2 endpoints (Cloud Run — production)

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | — | Health check |
| `GET` | `/metrics` | — | Prometheus metrics |
| `POST` | `/v2/extract` | `X-API-Key` | Single review → structured JSON |
| `POST` | `/v2/extract/batch` | `X-API-Key` | Async batch (1-100 reviews) |
| `GET` | `/v2/reviews` | `X-API-Key` | Query org's stored extractions |
| `GET` | `/v2/insights` | `X-API-Key` | Aggregated analytics for org |
| `POST` | `/admin/organizations` | HTTP Basic | Create org |
| `POST` | `/admin/organizations/{org_id}/keys` | HTTP Basic | Issue API key |
| `DELETE` | `/admin/organizations/{org_id}/keys/{key_id}` | HTTP Basic | Revoke key |
| `POST` | `/admin/organizations/{org_id}/keys/{key_id}/rotate` | HTTP Basic | Rotate key (atomic) |

### v1 endpoints (HF Spaces — legacy)

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/` | — | Analytics dashboard |
| `POST` | `/extract` | `X-API-Key` | Single review → JSON |
| `POST` | `/extract/batch` | `X-API-Key` | Async batch |
| `GET` | `/reviews` | `X-API-Key` | Query extractions |
| `GET` | `/insights` | `X-API-Key` | Aggregated analytics |

Interactive docs at `/docs` (Swagger) and `/redoc`.

---

## Tech stack

| Layer | Choice |
|---|---|
| API | FastAPI + Pydantic v2 |
| LLM (primary) | Groq — Llama 3.3 70B |
| LLM (fallback) | Google Gemini 2.0 Flash |
| Auth | argon2id-hashed API keys (per-org, per-tenant) |
| Storage (v2) | Supabase Postgres + asyncpg · RLS tenant isolation |
| Storage (v1) | SQLite + aiosqlite (WAL mode) |
| Observability | structlog (JSON) + Prometheus metrics |
| Hosting (v2) | Google Cloud Run (asia-south1) |
| Hosting (v1) | Hugging Face Spaces (Docker) |
| CI/CD | GitHub Actions |

---

## Self-host on Cloud Run

The production deployment runs on Google Cloud Run (free tier, `asia-south1`). To reproduce it:

1. **Provision Supabase** — create a project, run the migrations in `supabase/migrations/`
2. **Create GCP project** — enable `run`, `secretmanager`, `artifactregistry`, `cloudbuild` APIs
3. **Store secrets** in Secret Manager: `groq-api-key`, `gemini-api-key`, `supabase-database-url`, `admin-password-hash`
4. **Build and deploy** — follow [`ops/runbooks/cloud-run-deploy.md`](ops/runbooks/cloud-run-deploy.md)

Cost at free-tier traffic: **₹0.00/mo** (Cloud Run + Artifact Registry well within Always Free quotas). See [`ops/runbooks/cloud-run-cost-check.md`](ops/runbooks/cloud-run-cost-check.md) for the weekly cost-check procedure.

---

## Development

```bash
# Install uv (https://docs.astral.sh/uv/)
pip install uv

# Install deps
uv sync

# Copy and fill in secrets
cp .env.example .env

# Run locally
uv run uvicorn app.main:app --reload --port 8000

# Lint + format
uv run ruff check .
uv run ruff format .

# Type check
uv run mypy app/

# Tests (unit + integration)
uv run pytest

# Eval (requires GROQ_API_KEY)
uv run python -m eval.runner
uv run python -m eval.report
```

---

## CI

| Workflow | Trigger | Fails CI? |
|---|---|---|
| `ci.yml` | Every push / PR | Yes — lint, format, mypy, unit tests |
| `eval.yml` | Push touching `app/core/prompt.py`, `app/core/prompts/**`, `app/core/llm.py`, `app/core/schemas.py`, `eval/fixtures/**`, `eval/runner.py` · Nightly 02:00 UTC · `workflow_dispatch` | Yes — overall ≥ 85% and per-language ≥ 80% |
| `deploy.yml` | Push to `main` | No (informational) |

Eval is scoped to prompt/LLM/schema/fixture changes so normal PRs (docs, refactor) don't burn the free-tier Groq quota.

---

## Roadmap

**Phase 2.0a** ✓ (shipped May 2026):
- Multi-tenant architecture — per-org API keys, argon2id auth, Postgres + RLS isolation
- Cloud Run production deployment (asia-south1, free tier)
- Token accounting per usage record
- Admin API for org/key lifecycle management
- Kill switch — Pub/Sub budget alert → Cloud Function → Cloud Run traffic = 0

**Phase 2.0b** ✓ (shipped May 2026):
- Language detection — Devanagari regex + Hinglish keyword heuristics + lingua-py confidence
- Language-branched prompts (v2.0) — en / hi-en / hi, each with explicit English-output instruction
- 46-fixture eval suite — 25 English + 15 Hinglish (Claude Sonnet auto-labeled) + 6 Hindi (synthetic + verified)
- Per-language CI gate — overall ≥ 85%, each language ≥ 80%
- Nightly Slack drift alerts — eval results posted to channel after every scheduled run

**Phase 2.x** (planned):
- Webhook ingestion from Yotpo / Judge.me / Shopify
- Tamil, Marathi support
- Cost dashboard: tokens & $ per extraction over time

**Phase 3** (vision):
- Human-in-the-loop feedback → DPO fine-tuning
- Cross-review insight generation ("top 3 complaints this week")
- Auto-response drafting for CX
- Stripe billing integration
- Native integrations — Shopify app, WooCommerce plugin

---

## How Hinglish fixtures are labeled

Ground truth for Hinglish fixtures (`eval/fixtures/hi-en/`) is generated by Anthropic Claude Sonnet, an independent model from the production Groq Llama 3.3 70B being evaluated. This keeps the eval honest — a different model labels the data than the one being scored. Source reviews come from publicly available Flipkart Kaggle datasets, classified as Hinglish (Latin-script Hindi/English code-mix) via regex heuristics. All fixtures are committed and inspectable; anyone can audit the labels for quality.

---

## License

MIT — see [LICENSE](LICENSE).
