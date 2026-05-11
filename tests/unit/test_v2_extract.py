"""Unit tests for app.api.v2.extract — token accounting wiring."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from app.auth.api_key import ApiKeyContext
from app.core.schemas import ReviewExtractionLLMOutput, Sentiment, Urgency

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
    from app.core.schemas import ReviewRequest
    from app.api.v2.extract import _run_extraction_v2

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
    from app.core.schemas import ReviewRequest
    from app.api.v2.extract import _run_extraction_v2

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
    from app.core.schemas import ReviewRequest
    from app.api.v2.extract import _run_extraction_v2

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
    from app.core.schemas import ReviewRequest, ReviewExtractionV2, ExtractionMetaV2
    from app.api.v2.extract import _run_extraction_v2
    from datetime import datetime, timezone

    req = ReviewRequest(text=_REVIEW_TEXT)
    cached = ReviewExtractionV2(
        product="Test Widget",
        extraction_meta=ExtractionMetaV2(
            model="mock", prompt_version="v1", schema_version="1.0.0",
            extracted_at=datetime.now(tz=timezone.utc),
            input_hash="sha256:abc", org_id=_ORG_ID,
        ),
    )

    with (
        patch("app.api.v2.extract.get_by_hash_pg", return_value=cached),
        patch("app.api.v2.extract.update_usage_tokens") as mock_update,
    ):
        await _run_extraction_v2(req, _CTX)

    mock_update.assert_not_called()
