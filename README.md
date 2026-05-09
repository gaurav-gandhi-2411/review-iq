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

![Under Construction](https://img.shields.io/badge/status-under%20construction-yellow)
![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.11-blue)

**Production-grade review intelligence service. Unstructured customer reviews → queryable structured insights.**

> Turn rambling customer feedback into clean, queryable JSON — with sentiment, topics, competitor mentions, urgency signals, and more.

---

## What it does

```bash
curl -X POST https://<your-space>.hf.space/extract \
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
  "language": "en"
}
```

---

## Status

🚧 **Phase 1 in progress** — see [plan.md](plan.md) for full scope.

- [ ] `POST /extract` — single review → structured JSON
- [ ] `POST /extract/batch` — bulk processing
- [ ] `GET /reviews` — query stored extractions
- [ ] `GET /insights` — aggregated analytics
- [ ] Dashboard at `/`
- [ ] Eval suite (≥85% field accuracy on 25 fixtures)
- [ ] Live on Hugging Face Spaces

---

## Tech stack

| Layer | Choice |
|---|---|
| API | FastAPI |
| LLM (primary) | Groq — Llama 3.3 70B |
| LLM (fallback) | Google Gemini 1.5 Flash |
| DB | SQLite (Phase 1) |
| Hosting | Hugging Face Spaces |

---

## Development

```bash
# Install uv
pip install uv

# Create env and install deps
uv sync

# Run locally
uv run uvicorn app.main:app --reload

# Lint + format
uv run ruff check .
uv run ruff format .

# Tests
uv run pytest
```

---

## License

MIT — see [LICENSE](LICENSE).
