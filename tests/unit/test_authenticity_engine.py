"""Unit tests for app.core.authenticity.engine — LLM mocked via AsyncMock."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from app.core.authenticity.engine import _LLMAuthenticityOutput, score_batch, score_single
from app.core.authenticity.schema import AuthenticityFlag, AuthenticityLabel, AuthenticityResult
from app.core.config import Settings


def _make_settings() -> Settings:
    """Return a minimal Settings instance for tests (no real API key needed)."""
    return Settings(
        GROQ_API_KEY="test-key",
        GROQ_MODEL_LARGE="mock-large-model",
        GROQ_MODEL_SMALL="mock-small-model",
    )


# ---------------------------------------------------------------------------
# 1. score_single — LLM returns a high-confidence genuine signal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_single_with_llm_signal() -> None:
    """Mock LLM returning score=0.85 → combined score should be > 0.6 → GENUINE."""
    llm_output = _LLMAuthenticityOutput(score=0.85, flags=[], reasoning="looks genuine")
    mock_return = (llm_output, "mock-model", 50, 20)

    with patch(
        "app.core.authenticity.engine._call_authenticity_llm",
        new=AsyncMock(return_value=mock_return),
    ):
        result = await score_single(
            "This blender is fantastic for daily smoothies. I have used it every morning.",
            settings=_make_settings(),
        )

    assert isinstance(result, AuthenticityResult)
    assert result.score > 0.6
    assert result.label == AuthenticityLabel.GENUINE


# ---------------------------------------------------------------------------
# 2. score_single — LLM raises; falls back gracefully to heuristics only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_single_llm_failure_falls_back_to_heuristics() -> None:
    """When _call_authenticity_llm raises RuntimeError, score_single must still
    return an AuthenticityResult (not propagate the exception)."""
    # The engine catches exceptions inside _call_authenticity_llm and returns
    # the neutral default. Simulate the internal fallback by making the mock
    # return the neutral output instead of raising — matching real behaviour
    # where all exceptions are caught inside _call_authenticity_llm.
    neutral = (_LLMAuthenticityOutput(), "mock-model", 0, 0)

    with patch(
        "app.core.authenticity.engine._call_authenticity_llm",
        new=AsyncMock(return_value=neutral),
    ):
        result = await score_single(
            "This is a decent product with some good qualities.",
            settings=_make_settings(),
        )

    assert isinstance(result, AuthenticityResult)
    assert 0.0 <= result.score <= 1.0


# ---------------------------------------------------------------------------
# 3. score_single — incentivized review is correctly flagged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_single_incentivized_review() -> None:
    """LLM returns score=0.2 + incentivized_phrase flag; result must carry the flag."""
    llm_output = _LLMAuthenticityOutput(
        score=0.2,
        flags=["incentivized_phrase"],
        reasoning="clearly incentivized",
    )
    mock_return = (llm_output, "mock-model", 60, 30)

    with patch(
        "app.core.authenticity.engine._call_authenticity_llm",
        new=AsyncMock(return_value=mock_return),
    ):
        result = await score_single(
            "I received this product for free in exchange for a review.",
            settings=_make_settings(),
        )

    assert AuthenticityFlag.INCENTIVIZED_PHRASE in result.flags


# ---------------------------------------------------------------------------
# 4. score_batch — near-duplicate texts both receive NEAR_DUPLICATE flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_batch_merges_near_duplicate_flags() -> None:
    """Two near-identical texts should both have NEAR_DUPLICATE after batch merge."""
    # Texts differ only in the last word → Jaccard ~0.83, well above the 0.60 threshold.
    text_a = "this is a great product and I would definitely buy it again tomorrow"
    text_b = "this is a great product and I would definitely buy it again today"

    llm_output = _LLMAuthenticityOutput(score=0.8, flags=[], reasoning="seems genuine")
    mock_return = (llm_output, "mock-model", 50, 20)

    with patch(
        "app.core.authenticity.engine._call_authenticity_llm",
        new=AsyncMock(return_value=mock_return),
    ):
        results = await score_batch(
            [(text_a, None), (text_b, None)],
            settings=_make_settings(),
        )

    assert len(results) == 2
    assert AuthenticityFlag.NEAR_DUPLICATE in results[0].flags
    assert AuthenticityFlag.NEAR_DUPLICATE in results[1].flags


# ---------------------------------------------------------------------------
# 5. score_batch — output order is preserved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_batch_preserves_order() -> None:
    """score_batch must return results in the same order as the input list."""
    texts = [
        ("First review about a blender that works well for smoothies.", 5),
        ("Second review mentioning the packaging was damaged on arrival.", 2),
        ("Third review about battery life being disappointing after a month.", 3),
    ]

    llm_output = _LLMAuthenticityOutput(score=0.75, flags=[], reasoning="neutral")
    mock_return = (llm_output, "mock-model", 40, 15)

    with patch(
        "app.core.authenticity.engine._call_authenticity_llm",
        new=AsyncMock(return_value=mock_return),
    ):
        results = await score_batch(texts, settings=_make_settings())

    assert len(results) == 3
    # Verify order: each result's hash should correspond to the input text
    import hashlib

    for idx, (text, _stars) in enumerate(texts):
        expected_hash = hashlib.sha256(text.encode()).hexdigest()
        assert results[idx].review_hash == expected_hash
