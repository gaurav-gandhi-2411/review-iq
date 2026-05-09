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

[![CI](https://github.com/gaurav-gandhi-2411/review-iq/actions/workflows/ci.yml/badge.svg)](https://github.com/gaurav-gandhi-2411/review-iq/actions/workflows/ci.yml)
![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.11-blue)

**Production-grade review intelligence service. Unstructured customer reviews → queryable structured insights.**

> Turn rambling customer feedback into clean, queryable JSON — with sentiment, topics, competitor mentions, urgency signals, and more.

---

## Live demo

**API**: `https://gauravgandhi2411-review-iq.hf.space`

**Dashboard**: `https://gauravgandhi2411-review-iq.hf.space/`

---

## Quick start

```bash
# Extract structured insights from a review
curl -X POST https://gauravgandhi2411-review-iq.hf.space/extract \
  -H "X-API-Key: $API_KEY" \
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
    "prompt_version": "v1.0",
    "schema_version": "1.0.0",
    "latency_ms": 820
  }
}
```

### Batch processing

```bash
# Submit up to 100 reviews at once
curl -X POST https://gauravgandhi2411-review-iq.hf.space/extract/batch \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"reviews": [{"text": "Great product!"}, {"text": "Terrible quality, returned."}]}'
# → {"job_id": "abc-123", "status": "pending", "total": 2, ...}

# Poll for status
curl https://gauravgandhi2411-review-iq.hf.space/extract/batch/abc-123 \
  -H "X-API-Key: $API_KEY"
```

### Query and analytics

```bash
# Filter stored extractions
curl "https://gauravgandhi2411-review-iq.hf.space/reviews?sentiment=negative&urgency=high" \
  -H "X-API-Key: $API_KEY"

# Aggregated insights (sentiment breakdown, top topics, trend over time)
curl https://gauravgandhi2411-review-iq.hf.space/insights \
  -H "X-API-Key: $API_KEY"
```

---

## Eval results

Evaluated on 25 hand-labeled fixtures across all extraction scenarios:

| Metric | Score |
|---|---|
| Overall field accuracy | *run `python -m eval.runner` to populate* |
| Pass threshold | ≥ 85% |
| Fixtures | 25 (explicit stars, urgency, PII, multilingual, sarcasm, …) |

Eval runs automatically in CI on every push to `main`.

---

## API reference

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | — | Health check |
| `GET` | `/metrics` | — | Prometheus metrics |
| `GET` | `/` | — | Analytics dashboard |
| `POST` | `/extract` | `X-API-Key` | Single review → JSON |
| `POST` | `/extract/batch` | `X-API-Key` | Async batch (1-100 reviews) |
| `GET` | `/extract/batch/{job_id}` | `X-API-Key` | Poll batch job status |
| `GET` | `/reviews` | `X-API-Key` | Query stored extractions |
| `GET` | `/insights` | `X-API-Key` | Aggregated analytics |

Interactive docs at `/docs` (Swagger) and `/redoc`.

---

## Tech stack

| Layer | Choice |
|---|---|
| API | FastAPI + Pydantic v2 |
| LLM (primary) | Groq — Llama 3.3 70B |
| LLM (fallback) | Google Gemini 2.0 Flash |
| Storage | SQLite + aiosqlite (WAL mode) |
| Observability | structlog (JSON) + Prometheus metrics |
| Hosting | Hugging Face Spaces (Docker) |
| CI/CD | GitHub Actions |

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
| `eval.yml` | Push touching `app/core/prompt.py`, `app/core/llm.py`, `app/core/schemas.py`, `eval/fixtures/**`, `eval/runner.py` · Nightly 02:00 UTC · `workflow_dispatch` | Yes — overall accuracy must be ≥ 85% |
| `deploy.yml` | Push to `main` | No (informational) |

Eval is scoped to prompt/LLM/schema/fixture changes so normal PRs (docs, refactor) don't burn the free-tier Groq quota.

---

## Phase 2 / 3 vision

**Phase 2** (documented, not built yet):
- Webhook ingestion from Yotpo / Judge.me / Shopify
- Multi-language: Hindi, Tamil, Hinglish detection + translation
- Slack alerts on urgent reviews
- Vector search (sqlite-vss / pgvector) for semantic retrieval
- Drift monitoring: nightly fixture re-run, alert on field-level drift
- Cost dashboard: tokens & $ per extraction over time
- SQLite → Postgres migration

**Phase 3** (vision):
- Human-in-the-loop feedback → DPO fine-tuning
- Cross-review insight generation ("top 3 complaints this week")
- Auto-response drafting for CX
- Multi-tenancy + Stripe billing
- Native integrations — Shopify app, WooCommerce plugin

---

## License

MIT — see [LICENSE](LICENSE).
