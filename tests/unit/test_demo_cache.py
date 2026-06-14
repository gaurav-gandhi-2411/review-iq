"""Unit tests for demo endpoint in-memory LRU cache (token-conservation).

Asserts:
- Two identical demo requests call the LLM exactly once (second is cache-served).
- Different review text calls the LLM each time.
- The cache is bounded at _DEMO_CACHE_MAX_SIZE (does not grow unboundedly).
- Cache-miss path returns a valid ReviewExtraction with the expected shape.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from app.core.schemas import ReviewExtractionLLMOutput, Sentiment, Urgency
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Shared fake LLM output
# ---------------------------------------------------------------------------

_LLM_OUTPUT = ReviewExtractionLLMOutput(
    product="Demo Widget",
    stars=4,
    sentiment=Sentiment.positive,
    urgency=Urgency.low,
    topics=["quality"],
    competitor_mentions=[],
    pros=["solid"],
    cons=[],
    feature_requests=[],
    language="en",
    confidence=0.88,
)


@pytest.fixture(autouse=True)
def _clear_demo_cache() -> None:  # type: ignore[return]
    """Ensure each test starts with an empty demo cache."""
    from app.api.demo import demo_cache_clear

    demo_cache_clear()
    yield
    demo_cache_clear()


@pytest.fixture
def client() -> TestClient:
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Core caching behaviour
# ---------------------------------------------------------------------------


def test_identical_text_calls_llm_once(client: TestClient) -> None:
    """Two POSTs with the same review text must call extract_with_llm exactly once."""
    text = "This demo widget is absolutely wonderful for everyday use!"

    with patch(
        "app.api.demo.extract_with_llm",
        new=AsyncMock(return_value=(_LLM_OUTPUT, "mock-model", 50, 0, 0, False)),
    ) as mock_llm:
        resp1 = client.post("/demo/extract", json={"text": text})
        resp2 = client.post("/demo/extract", json={"text": text})

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert mock_llm.call_count == 1, f"Expected 1 LLM call, got {mock_llm.call_count}"


def test_identical_text_second_response_matches_first(client: TestClient) -> None:
    """Cached response must equal the first response (same product / label)."""
    text = "Great build quality, ships fast."

    with patch(
        "app.api.demo.extract_with_llm",
        new=AsyncMock(return_value=(_LLM_OUTPUT, "mock-model", 50, 0, 0, False)),
    ):
        body1 = client.post("/demo/extract", json={"text": text}).json()
        body2 = client.post("/demo/extract", json={"text": text}).json()

    assert body1["product"] == body2["product"]
    assert body1["sentiment"] == body2["sentiment"]


def test_different_text_calls_llm_each_time(client: TestClient) -> None:
    """Different review texts each require a fresh LLM call — no cross-key collisions."""
    text_a = "Absolutely love this product, five stars!"
    text_b = "Terrible packaging, arrived broken."

    with patch(
        "app.api.demo.extract_with_llm",
        new=AsyncMock(return_value=(_LLM_OUTPUT, "mock-model", 50, 0, 0, False)),
    ) as mock_llm:
        client.post("/demo/extract", json={"text": text_a})
        client.post("/demo/extract", json={"text": text_b})

    assert mock_llm.call_count == 2, f"Expected 2 LLM calls, got {mock_llm.call_count}"


# ---------------------------------------------------------------------------
# Bounded cache / LRU eviction
# ---------------------------------------------------------------------------


def test_cache_does_not_exceed_max_size() -> None:
    """Inserting more than _DEMO_CACHE_MAX_SIZE distinct entries evicts oldest; size stays bounded."""
    from app.api.demo import (
        _DEMO_CACHE_MAX_SIZE,
        _demo_cache_key,
        _demo_cache_put,
        demo_cache_size,
    )
    from app.core.schemas import ExtractionMeta, ReviewExtraction

    def _make_result(seed: str) -> ReviewExtraction:
        meta = ExtractionMeta(
            model="t",
            prompt_version="v1",
            schema_version="1.0.0",
            extracted_at=datetime.utcnow(),
            latency_ms=1,
            input_hash=f"sha256:{hashlib.sha256(seed.encode()).hexdigest()}",
        )
        return ReviewExtraction(
            product=f"product-{seed}",
            extraction_meta=meta,
        )

    # Insert _DEMO_CACHE_MAX_SIZE + 10 distinct entries.
    for i in range(_DEMO_CACHE_MAX_SIZE + 10):
        key = _demo_cache_key(f"unique-text-{i}")
        _demo_cache_put(key, _make_result(f"unique-text-{i}"))

    assert demo_cache_size() == _DEMO_CACHE_MAX_SIZE, (
        f"Cache grew to {demo_cache_size()}, expected {_DEMO_CACHE_MAX_SIZE}"
    )


def test_cache_evicts_lru_entry() -> None:
    """Inserting past capacity evicts the least-recently-used entry."""
    from app.api.demo import (
        _DEMO_CACHE_MAX_SIZE,
        _demo_cache_get,
        _demo_cache_key,
        _demo_cache_put,
    )
    from app.core.schemas import ExtractionMeta, ReviewExtraction

    def _make_result(seed: str) -> ReviewExtraction:
        meta = ExtractionMeta(
            model="t",
            prompt_version="v1",
            schema_version="1.0.0",
            extracted_at=datetime.utcnow(),
            latency_ms=1,
            input_hash=f"sha256:{hashlib.sha256(seed.encode()).hexdigest()}",
        )
        return ReviewExtraction(product=f"product-{seed}", extraction_meta=meta)

    # First entry — will be the LRU once we fill the cache.
    first_key = _demo_cache_key("first-entry")
    _demo_cache_put(first_key, _make_result("first-entry"))

    # Fill the rest of the cache.
    for i in range(_DEMO_CACHE_MAX_SIZE - 1):
        k = _demo_cache_key(f"filler-{i}")
        _demo_cache_put(k, _make_result(f"filler-{i}"))

    # One more insert should evict "first-entry".
    overflow_key = _demo_cache_key("overflow-entry")
    _demo_cache_put(overflow_key, _make_result("overflow-entry"))

    assert _demo_cache_get(first_key) is None, "LRU entry should have been evicted"
    assert _demo_cache_get(overflow_key) is not None, "Overflow entry should be present"


# ---------------------------------------------------------------------------
# Cache key correctness
# ---------------------------------------------------------------------------


def test_demo_cache_key_is_stable() -> None:
    """Same text always produces the same cache key."""
    from app.api.demo import _demo_cache_key

    text = "Stable review text."
    assert _demo_cache_key(text) == _demo_cache_key(text)


def test_demo_cache_key_differs_by_text() -> None:
    """Different texts produce different cache keys (no collision on distinct inputs)."""
    from app.api.demo import _demo_cache_key

    assert _demo_cache_key("text one") != _demo_cache_key("text two")


# ---------------------------------------------------------------------------
# Cache miss path — normal extraction still works
# ---------------------------------------------------------------------------


def test_cache_miss_returns_valid_extraction(client: TestClient) -> None:
    """A cold cache hit produces a valid ReviewExtraction response body."""
    with patch(
        "app.api.demo.extract_with_llm",
        new=AsyncMock(return_value=(_LLM_OUTPUT, "mock-model", 50, 0, 0, False)),
    ):
        resp = client.post("/demo/extract", json={"text": "Fresh review never seen before."})

    assert resp.status_code == 200
    body = resp.json()
    assert body["product"] == "Demo Widget"
    assert body["sentiment"] == "positive"
    assert "extraction_meta" in body
