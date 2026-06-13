"""Tests for large-model quota-cap graceful degradation (Change #2).

All providers are mocked — no live Groq or Gemini calls.

Covers:
- _is_quota_error: 429/rate_limit → True; 500/timeout → False.
- Large quota during escalation → small result returned with degraded=True, HTTP 200.
- Non-quota large failure during escalation → still raises → HTTP 503.
- Large quota during escalation but no valid small result → propagates (raises).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import app.core.router as router_module
import pytest
from app.core.config import Settings
from app.core.router import _is_quota_error
from app.core.routing_policy import CONFIDENCE_ESCALATION_THRESHOLD
from app.core.schemas import ReviewExtractionLLMOutput, Sentiment
from groq import APIStatusError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LOW_CONF = ReviewExtractionLLMOutput(
    product="Widget",
    sentiment=Sentiment.positive,
    confidence=CONFIDENCE_ESCALATION_THRESHOLD - 0.15,  # triggers low_confidence escalation
)
_HIGH_CONF = ReviewExtractionLLMOutput(
    product="Widget",
    sentiment=Sentiment.positive,
    confidence=0.95,
)
_LOW_CONF_RAW = json.dumps(_LOW_CONF.model_dump())
_HIGH_CONF_RAW = json.dumps(_HIGH_CONF.model_dump())


def _settings(**overrides: object) -> Settings:
    base = dict(
        GROQ_API_KEY="fake-key",
        GROQ_MODEL_SMALL="llama-3.1-8b-instant",
        GROQ_MODEL_LARGE="llama-3.3-70b-versatile",
        LLM_MAX_RETRIES=0,
        LLM_TIMEOUT_SECONDS=30,
    )
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


def _quota_api_error() -> APIStatusError:
    """Return a Groq 429 APIStatusError (quota / TPD cap)."""
    return APIStatusError(
        "rate_limit_exceeded",
        response=MagicMock(status_code=429, headers={}),
        body={"error": {"code": "rate_limit_exceeded"}},
    )


def _generic_500_error() -> APIStatusError:
    """Return a Groq 500 APIStatusError (non-quota server error)."""
    return APIStatusError(
        "internal server error",
        response=MagicMock(status_code=500, headers={}),
        body={},
    )


# ---------------------------------------------------------------------------
# _is_quota_error unit tests
# ---------------------------------------------------------------------------


class TestIsQuotaError:
    def test_429_api_status_error_returns_true(self) -> None:
        assert _is_quota_error(_quota_api_error()) is True

    def test_rate_limit_exceeded_message_returns_true(self) -> None:
        exc = RuntimeError("Groq provider exhausted: rate_limit_exceeded")
        assert _is_quota_error(exc) is True

    def test_tokens_per_day_message_returns_true(self) -> None:
        exc = RuntimeError("tokens per day limit reached")
        assert _is_quota_error(exc) is True

    def test_tpd_message_returns_true(self) -> None:
        exc = RuntimeError("TPD cap exceeded for this organisation")
        assert _is_quota_error(exc) is True

    def test_rate_limit_substring_returns_true(self) -> None:
        exc = RuntimeError("Rate limit hit, retry after 60s")
        assert _is_quota_error(exc) is True

    def test_runtime_error_wrapping_429_via_cause_returns_true(self) -> None:
        cause = _quota_api_error()
        exc = RuntimeError("Groq provider exhausted: rate_limit_exceeded")
        exc.__cause__ = cause
        assert _is_quota_error(exc) is True

    def test_500_api_status_error_returns_false(self) -> None:
        assert _is_quota_error(_generic_500_error()) is False

    def test_generic_runtime_error_returns_false(self) -> None:
        exc = RuntimeError("connection timed out")
        assert _is_quota_error(exc) is False

    def test_value_error_returns_false(self) -> None:
        exc = ValueError("unexpected response format")
        assert _is_quota_error(exc) is False

    def test_runtime_error_wrapping_500_via_cause_returns_false(self) -> None:
        cause = _generic_500_error()
        exc = RuntimeError("Groq provider exhausted: internal server error")
        exc.__cause__ = cause
        assert _is_quota_error(exc) is False


# ---------------------------------------------------------------------------
# route_extraction: large quota during escalation → degraded small result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_large_quota_during_escalation_returns_degraded_small() -> None:
    """Low confidence from small triggers escalation; large hits 429 quota cap.

    Expected: router returns the small extraction with degraded=True (not raise).
    """
    settings = _settings()
    prompt = "This product is absolutely fantastic and works great every time."

    call_count = 0

    async def fake_complete(
        user_prompt: str,
        *,
        system_prompt: str,
        retry: bool = False,
        timeout: int | None = None,
    ) -> tuple[str, int, int]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _LOW_CONF_RAW, 8, 4  # small: low confidence → escalation triggers
        # Large model call: raise a 429 wrapped as RuntimeError (as _call_provider does)
        quota_exc = _quota_api_error()
        raise RuntimeError(f"Groq provider exhausted: {quota_exc}") from quota_exc

    with patch("app.core.router.GroqProvider") as MockProvider:
        instance = MockProvider.return_value
        instance.model = settings.groq_model_small
        instance.trains_on_input = False
        instance.complete = fake_complete

        extraction, model, tin, tout, escalated, degraded = await router_module.route_extraction(
            prompt, "system", allow_gemini_fallback=False, settings=settings
        )

    # Must have tried both small and large (call_count == 2).
    assert call_count == 2, f"Expected 2 provider calls, got {call_count}"
    # Degraded flag must be set.
    assert degraded is True
    # Result must be the small model extraction (low confidence value preserved).
    assert extraction.confidence == pytest.approx(_LOW_CONF.confidence)
    # escalated=True because escalation was triggered (even though large was capped).
    assert escalated is True
    # Model reported is the small model (the one that actually served the response).
    assert model == settings.groq_model_small


# ---------------------------------------------------------------------------
# route_extraction: non-quota large failure still raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_quota_large_failure_during_escalation_raises() -> None:
    """Low confidence → escalation; large model fails with 500 (non-quota).

    Expected: RuntimeError propagates — caller must 503, not silently degrade.
    """
    settings = _settings()
    prompt = "This product is absolutely fantastic and works great every time."

    call_count = 0

    async def fake_complete(
        user_prompt: str,
        *,
        system_prompt: str,
        retry: bool = False,
        timeout: int | None = None,
    ) -> tuple[str, int, int]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _LOW_CONF_RAW, 8, 4  # small: low confidence
        server_exc = _generic_500_error()
        raise RuntimeError(f"Groq provider exhausted: {server_exc}") from server_exc

    with patch("app.core.router.GroqProvider") as MockProvider:
        instance = MockProvider.return_value
        instance.trains_on_input = False
        instance.complete = fake_complete

        with pytest.raises(RuntimeError):
            await router_module.route_extraction(
                prompt, "system", allow_gemini_fallback=False, settings=settings
            )

    assert call_count == 2


# ---------------------------------------------------------------------------
# route_extraction: large quota during escalation but no valid small result
# (small model schema failed → extraction is None) → still raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_large_quota_with_no_small_result_raises() -> None:
    """Small model returns unparseable JSON (schema_invalid) AND large hits quota.

    No valid small extraction exists, so degraded fallback is impossible → raise.
    """
    settings = _settings()
    prompt = "This product is absolutely fantastic."

    call_count = 0

    async def fake_complete(
        user_prompt: str,
        *,
        system_prompt: str,
        retry: bool = False,
        timeout: int | None = None,
    ) -> tuple[str, int, int]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "not valid json {{{{", 8, 4  # small: bad JSON → schema_valid=False
        quota_exc = _quota_api_error()
        raise RuntimeError(f"Groq provider exhausted: {quota_exc}") from quota_exc

    with patch("app.core.router.GroqProvider") as MockProvider:
        instance = MockProvider.return_value
        instance.trains_on_input = False
        instance.complete = fake_complete

        with pytest.raises(RuntimeError):
            await router_module.route_extraction(
                prompt, "system", allow_gemini_fallback=False, settings=settings
            )

    assert call_count == 2


# ---------------------------------------------------------------------------
# Full pipeline: large quota → degraded → HTTP 200, not 503
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_api_returns_200_with_degraded_flag_on_large_quota() -> None:
    """End-to-end: large quota cap during escalation → HTTP 200 with degraded=True in meta.

    Uses the full extract_with_llm path with tiered routing enabled.
    Mocks: route_extraction → returns degraded=True result.
    """
    import uuid

    from app.auth.api_key import ApiKeyContext, require_api_key
    from app.main import app
    from fastapi.testclient import TestClient

    org_id = str(uuid.uuid4())
    key_id = str(uuid.uuid4())
    usage_id = str(uuid.uuid4())
    ctx = ApiKeyContext(
        org_id=org_id,
        api_key_id=key_id,
        key_name="test-key",
        usage_record_id=usage_id,
    )

    degraded_extraction = _LOW_CONF

    app.dependency_overrides[require_api_key] = lambda: ctx
    try:
        with (
            patch("app.api.v2.extract.get_by_hash_pg", return_value=None),
            patch("app.api.v2.extract.save_extraction_pg", return_value=str(uuid.uuid4())),
            patch("app.api.v2.extract.update_usage_tokens"),
            patch(
                "app.api.v2.extract.extract_with_llm",
                new=AsyncMock(
                    return_value=(degraded_extraction, "llama-3.1-8b-instant", 42, 10, 5, True)
                ),
            ),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/v2/extract",
                json={"text": "Great product, totally recommend it!"},
            )

        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        data = response.json()
        assert data["extraction_meta"]["degraded"] is True
        assert data["product"] == "Widget"
    finally:
        app.dependency_overrides.pop(require_api_key, None)


# ---------------------------------------------------------------------------
# Full pipeline: non-quota large failure → HTTP 503 (unchanged behaviour)
# ---------------------------------------------------------------------------


def test_v2_api_returns_503_on_non_quota_large_failure() -> None:
    """Non-quota RuntimeError from extract_with_llm still produces HTTP 503."""
    import uuid

    from app.auth.api_key import ApiKeyContext, require_api_key
    from app.main import app
    from fastapi.testclient import TestClient

    org_id = str(uuid.uuid4())
    key_id = str(uuid.uuid4())
    usage_id = str(uuid.uuid4())
    ctx = ApiKeyContext(
        org_id=org_id,
        api_key_id=key_id,
        key_name="test-key",
        usage_record_id=usage_id,
    )

    app.dependency_overrides[require_api_key] = lambda: ctx
    try:
        with (
            patch("app.api.v2.extract.get_by_hash_pg", return_value=None),
            patch(
                "app.api.v2.extract.extract_with_llm",
                new=AsyncMock(side_effect=RuntimeError("large model 500, no fallback")),
            ),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/v2/extract",
                json={"text": "Great product!"},
            )

        assert response.status_code == 503
    finally:
        app.dependency_overrides.pop(require_api_key, None)
