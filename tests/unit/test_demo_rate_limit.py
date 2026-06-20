"""Rate-limit enforcement tests for the public /demo/extract endpoint.

The endpoint has a per-IP limit of 5 requests/minute (enforced by @limiter.limit).
The 6th request from the same IP must return 429 — this guards against LLM-cost drain.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.schemas import ReviewExtractionLLMOutput, Sentiment, Urgency

_LLM_OUTPUT = ReviewExtractionLLMOutput(
    product="Rate Widget",
    stars=None,
    sentiment=Sentiment.positive,
    urgency=Urgency.low,
    topics=[],
    competitor_mentions=[],
    pros=["good"],
    cons=[],
    feature_requests=[],
    language="en",
    confidence=0.9,
)


@pytest.fixture
def client() -> TestClient:
    from app.api.demo import demo_cache_clear
    from app.main import app

    demo_cache_clear()
    return TestClient(app, raise_server_exceptions=False)


def test_demo_extract_returns_429_after_limit(client: TestClient) -> None:
    """Requests 1–5 must succeed; request 6 must return 429."""
    with patch(
        "app.api.demo.extract_with_llm",
        new=AsyncMock(return_value=(_LLM_OUTPUT, "mock-model", 10, 0, 0, False)),
    ):
        for i in range(5):
            resp = client.post("/demo/extract", json={"text": f"review number {i} unique"})
            assert resp.status_code == 200, f"Request {i + 1} should be 200, got {resp.status_code}"

        # 6th unique text bypasses the demo cache so the rate limiter is definitely invoked
        resp = client.post("/demo/extract", json={"text": "sixth unique review triggers limit"})
        assert resp.status_code == 429, f"Expected 429, got {resp.status_code}: {resp.text}"


def test_demo_extract_cached_hit_counts_toward_limit(client: TestClient) -> None:
    """Cache hits still count toward the rate limit (checked before cache lookup)."""
    text = "Repeated review text for rate limit test."
    with patch(
        "app.api.demo.extract_with_llm",
        new=AsyncMock(return_value=(_LLM_OUTPUT, "mock-model", 10, 0, 0, False)),
    ):
        for i in range(5):
            resp = client.post("/demo/extract", json={"text": text})
            assert resp.status_code == 200

        resp = client.post("/demo/extract", json={"text": text})
        assert resp.status_code == 429
