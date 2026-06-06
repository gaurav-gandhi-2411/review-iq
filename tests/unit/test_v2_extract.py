"""Unit tests for app.api.v2.extract — token accounting wiring."""

from __future__ import annotations

import uuid
from datetime import UTC
from unittest.mock import AsyncMock, patch

import pytest
from app.auth.api_key import ApiKeyContext, require_api_key
from app.core.schemas import ReviewExtractionLLMOutput, ReviewRequest, Sentiment, Urgency
from fastapi.testclient import TestClient

_ORG_ID = str(uuid.uuid4())
_KEY_ID = str(uuid.uuid4())
_USAGE_ID = str(uuid.uuid4())

_CTX = ApiKeyContext(
    org_id=_ORG_ID,
    api_key_id=_KEY_ID,
    key_name="test-key",
    usage_record_id=_USAGE_ID,
)

_LLM_OUTPUT = ReviewExtractionLLMOutput(
    product="Test Widget",
    stars=5,
    sentiment=Sentiment.positive,
    urgency=Urgency.low,
    topics=["quality"],
    competitor_mentions=[],
    pros=["durable"],
    cons=[],
    language="en",
    confidence=0.9,
)

_REVIEW_TEXT = "This widget is absolutely fantastic!"


async def _run(tokens_in: int = 150, tokens_out: int = 80) -> None:
    from app.api.v2.extract import _run_extraction_v2
    from app.core.schemas import ReviewRequest

    req = ReviewRequest(text=_REVIEW_TEXT)

    with (
        patch("app.api.v2.extract.get_by_hash_pg", return_value=None),
        patch("app.api.v2.extract.save_extraction_pg", return_value=str(uuid.uuid4())),
        patch(
            "app.api.v2.extract.extract_with_llm",
            new=AsyncMock(return_value=(_LLM_OUTPUT, "mock-model", 42, tokens_in, tokens_out)),
        ),
        patch("app.api.v2.extract.update_usage_tokens") as mock_update,
    ):
        await _run_extraction_v2(req, _CTX)
        return mock_update


@pytest.mark.asyncio
async def test_update_usage_tokens_called_with_llm_token_counts() -> None:
    """After a successful LLM call, update_usage_tokens receives the correct counts."""
    from app.api.v2.extract import _run_extraction_v2
    from app.core.schemas import ReviewRequest

    req = ReviewRequest(text=_REVIEW_TEXT)

    with (
        patch("app.api.v2.extract.get_by_hash_pg", return_value=None),
        patch("app.api.v2.extract.save_extraction_pg", return_value=str(uuid.uuid4())),
        patch(
            "app.api.v2.extract.extract_with_llm",
            new=AsyncMock(return_value=(_LLM_OUTPUT, "mock-model", 42, 150, 80)),
        ),
        patch("app.api.v2.extract.update_usage_tokens") as mock_update,
    ):
        await _run_extraction_v2(req, _CTX)

    mock_update.assert_called_once_with(_USAGE_ID, 150, 80)


@pytest.mark.asyncio
async def test_update_usage_tokens_called_with_zero_when_provider_skips() -> None:
    """Tokens of 0 from provider still get written (not silently dropped)."""
    from app.api.v2.extract import _run_extraction_v2
    from app.core.schemas import ReviewRequest

    req = ReviewRequest(text=_REVIEW_TEXT)

    with (
        patch("app.api.v2.extract.get_by_hash_pg", return_value=None),
        patch("app.api.v2.extract.save_extraction_pg", return_value=str(uuid.uuid4())),
        patch(
            "app.api.v2.extract.extract_with_llm",
            new=AsyncMock(return_value=(_LLM_OUTPUT, "mock-model", 42, 0, 0)),
        ),
        patch("app.api.v2.extract.update_usage_tokens") as mock_update,
    ):
        await _run_extraction_v2(req, _CTX)

    mock_update.assert_called_once_with(_USAGE_ID, 0, 0)


@pytest.mark.asyncio
async def test_update_usage_tokens_not_called_on_cache_hit() -> None:
    """Cache hits don't create a new LLM call, so no token update happens."""
    from datetime import datetime

    from app.api.v2.extract import _run_extraction_v2
    from app.core.schemas import ExtractionMetaV2, ReviewExtractionV2, ReviewRequest

    req = ReviewRequest(text=_REVIEW_TEXT)
    cached = ReviewExtractionV2(
        product="Test Widget",
        extraction_meta=ExtractionMetaV2(
            model="mock",
            prompt_version="v1",
            schema_version="1.0.0",
            extracted_at=datetime.now(tz=UTC),
            input_hash="sha256:abc",
            org_id=_ORG_ID,
        ),
    )

    with (
        patch("app.api.v2.extract.get_by_hash_pg", return_value=cached),
        patch("app.api.v2.extract.update_usage_tokens") as mock_update,
    ):
        await _run_extraction_v2(req, _CTX)

    mock_update.assert_not_called()


# ---------------------------------------------------------------------------
# Line 45 — suspicious-input warning branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suspicious_input_logs_warning_and_still_calls_update_usage() -> None:
    """Text containing 'jailbreak' triggers is_suspicious=True; extraction still completes."""
    from app.api.v2.extract import _run_extraction_v2

    req = ReviewRequest(text="This product is a total jailbreak of expectations!")

    with (
        patch("app.api.v2.extract.get_by_hash_pg", return_value=None),
        patch("app.api.v2.extract.save_extraction_pg", return_value=str(uuid.uuid4())),
        patch(
            "app.api.v2.extract.extract_with_llm",
            new=AsyncMock(return_value=(_LLM_OUTPUT, "mock-model", 42, 150, 80)),
        ),
        patch("app.api.v2.extract.update_usage_tokens") as mock_update,
    ):
        await _run_extraction_v2(req, _CTX)

    mock_update.assert_called_once()


# ---------------------------------------------------------------------------
# Lines 112-115 — extract_single RuntimeError → 503
# ---------------------------------------------------------------------------


def test_extract_single_llm_down_returns_503() -> None:
    """RuntimeError from the LLM layer is converted to a 503 response."""
    from app.main import app

    app.dependency_overrides[require_api_key] = lambda: _CTX
    try:
        with patch(
            "app.api.v2.extract.extract_with_llm",
            new=AsyncMock(side_effect=RuntimeError("llm down")),
        ):
            # get_by_hash_pg must return None so the LLM path is reached
            with patch("app.api.v2.extract.get_by_hash_pg", return_value=None):
                client = TestClient(app, raise_server_exceptions=False)
                response = client.post(
                    "/v2/extract",
                    json={"text": "Any review text"},
                )
        assert response.status_code == 503
    finally:
        app.dependency_overrides.pop(require_api_key, None)


# ---------------------------------------------------------------------------
# Lines 122-132 — _process_batch_v2 function body
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_batch_v2_happy_path() -> None:
    """_process_batch_v2 processes all reviews without raising."""
    from app.api.v2.extract import _process_batch_v2

    reviews = [ReviewRequest(text="Good product"), ReviewRequest(text="Bad product")]

    with (
        patch("app.api.v2.extract.get_by_hash_pg", return_value=None),
        patch("app.api.v2.extract.save_extraction_pg", return_value=str(uuid.uuid4())),
        patch(
            "app.api.v2.extract.extract_with_llm",
            new=AsyncMock(return_value=(_LLM_OUTPUT, "mock-model", 42, 150, 80)),
        ),
        patch("app.api.v2.extract.update_usage_tokens"),
    ):
        # Should complete without raising any exception
        await _process_batch_v2(_CTX, reviews)


@pytest.mark.asyncio
async def test_process_batch_v2_swallows_per_item_error() -> None:
    """When the first review raises RuntimeError, the second still runs and no exception escapes."""
    from app.api.v2.extract import _process_batch_v2

    reviews = [ReviewRequest(text="Good product"), ReviewRequest(text="Bad product")]

    # First call raises, second call succeeds
    side_effects = [RuntimeError("oops"), (_LLM_OUTPUT, "mock-model", 42, 150, 80)]
    call_count = 0

    async def _mock_extract(*args: object, **kwargs: object) -> object:
        nonlocal call_count
        result = side_effects[call_count]
        call_count += 1
        if isinstance(result, Exception):
            raise result
        return result

    with (
        patch("app.api.v2.extract.get_by_hash_pg", return_value=None),
        patch("app.api.v2.extract.save_extraction_pg", return_value=str(uuid.uuid4())),
        patch("app.api.v2.extract.extract_with_llm", new=_mock_extract),
        patch("app.api.v2.extract.update_usage_tokens"),
    ):
        # Must not raise — errors are swallowed per the except Exception block
        await _process_batch_v2(_CTX, reviews)


# ---------------------------------------------------------------------------
# Lines 146-148 — extract_batch endpoint body
# ---------------------------------------------------------------------------


def test_extract_batch_returns_202_accepted() -> None:
    """POST /v2/extract/batch returns 202 with {status, total} immediately."""
    from app.main import app

    app.dependency_overrides[require_api_key] = lambda: _CTX
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/v2/extract/batch",
            json={"reviews": [{"text": "Good product"}, {"text": "Bad product"}]},
        )
        assert response.status_code == 202
        assert response.json() == {"status": "accepted", "total": "2"}
    finally:
        app.dependency_overrides.pop(require_api_key, None)
