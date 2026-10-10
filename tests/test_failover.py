"""Failover orchestration tests — secondary provider and 503 paths."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import app.core.llm as llm_module
import pytest
from app.core.config import Settings
from app.core.schemas import ReviewExtractionLLMOutput
from groq import APIStatusError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GOOD_EXTRACTION = ReviewExtractionLLMOutput(
    sentiment="positive",
    stars=None,
    buy_again=True,
    pros=["good quality"],
    cons=[],
    topics=["quality"],
    language="en",
    confidence=0.9,
)
_GOOD_RAW = json.dumps(_GOOD_EXTRACTION.model_dump())

_BASE_SETTINGS = dict(
    GROQ_API_KEY="fake-groq-key",
    SECONDARY_PROVIDER_API_KEY="",
    SECONDARY_PROVIDER_MODEL="",
    GROQ_MODEL="llama-3.3-70b-versatile",
    LLM_MAX_RETRIES=0,
    LLM_TIMEOUT_SECONDS=30,
)


def _settings(**overrides: object) -> Settings:
    return Settings(**{**_BASE_SETTINGS, **overrides})  # type: ignore[arg-type]


def _api_error() -> APIStatusError:
    return APIStatusError(
        message="Service unavailable",
        response=MagicMock(status_code=503, headers={}),
        body={"error": {"message": "Service unavailable"}},
    )


# ---------------------------------------------------------------------------
# Groq succeeds — baseline (no failover needed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_groq_success_no_failover(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_module, "get_settings", lambda: _settings())
    with patch("app.core.providers.groq.AsyncGroq") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_response = AsyncMock()
        mock_response.choices = [AsyncMock()]
        mock_response.choices[0].message.content = _GOOD_RAW
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        result, model, latency_ms, tin, tout, degraded = await llm_module.extract_with_llm(
            "test prompt"
        )

    assert result.sentiment == "positive"
    # Tiered routing (default-on) selects groq_model_small for this English input,
    # not the GROQ_MODEL override above -- was "llama" in model (matched
    # groq_model_small's old default coincidentally, not the actual override).
    assert model == "openai/gpt-oss-20b"
    assert not degraded


# ---------------------------------------------------------------------------
# API error → retry once → secondary (when configured)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_groq_api_error_failover_to_secondary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Groq fails with API error → secondary is called and returns a result."""
    monkeypatch.setattr(
        llm_module,
        "get_settings",
        lambda: _settings(
            SECONDARY_PROVIDER_API_KEY="fake-secondary-key",
            SECONDARY_PROVIDER_MODEL="some-secondary-model",
        ),
    )

    with patch("app.core.providers.groq.AsyncGroq") as mock_groq:
        mock_client = mock_groq.return_value
        mock_client.chat.completions.create = AsyncMock(side_effect=_api_error())

        with patch(
            "app.core.providers.secondary.SecondaryProvider.complete",
            new_callable=AsyncMock,
            return_value=(_GOOD_RAW, 8, 4),
        ):
            result, model, latency_ms, tin, tout, degraded = await llm_module.extract_with_llm(
                "test prompt"
            )

    assert result.sentiment == "positive"
    assert model == "some-secondary-model"
    assert not degraded


# ---------------------------------------------------------------------------
# API error → secondary also fails → RuntimeError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_groq_and_secondary_both_fail_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        llm_module,
        "get_settings",
        lambda: _settings(
            SECONDARY_PROVIDER_API_KEY="fake-secondary-key",
            SECONDARY_PROVIDER_MODEL="some-secondary-model",
        ),
    )

    with patch("app.core.providers.groq.AsyncGroq") as mock_groq:
        mock_client = mock_groq.return_value
        mock_client.chat.completions.create = AsyncMock(side_effect=_api_error())

        with patch(
            "app.core.providers.secondary.SecondaryProvider.complete",
            new_callable=AsyncMock,
            side_effect=RuntimeError("secondary also down"),
        ):
            with pytest.raises(Exception):
                await llm_module.extract_with_llm("test prompt")


# ---------------------------------------------------------------------------
# (the Gemini demo-path fallback was retired in S17, ADR 0035)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_providers_fail_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_module, "get_settings", lambda: _settings())

    with patch("app.core.providers.groq.AsyncGroq") as mock_groq:
        mock_client = mock_groq.return_value
        mock_client.chat.completions.create = AsyncMock(side_effect=_api_error())

        with pytest.raises(RuntimeError, match="All LLM providers failed"):
            await llm_module.extract_with_llm("test prompt")


# ---------------------------------------------------------------------------
# Secondary with trains_on_input=True is rejected (privacy violation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_secondary_trains_on_input_raises_privacy_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A secondary provider with trains_on_input=True must be rejected."""
    from app.core.providers import secondary as secondary_module

    monkeypatch.setattr(
        llm_module,
        "get_settings",
        lambda: _settings(
            SECONDARY_PROVIDER_API_KEY="fake-key",
            SECONDARY_PROVIDER_MODEL="bad-model",
        ),
    )

    # Temporarily set trains_on_input=True on SecondaryProvider to simulate misconfiguration.
    original = secondary_module.SecondaryProvider.trains_on_input
    try:
        secondary_module.SecondaryProvider.trains_on_input = True  # type: ignore[assignment]

        with patch("app.core.providers.groq.AsyncGroq") as mock_groq:
            mock_client = mock_groq.return_value
            mock_client.chat.completions.create = AsyncMock(side_effect=_api_error())

            with pytest.raises(RuntimeError, match="trains on input"):
                await llm_module.extract_with_llm("test prompt")
    finally:
        secondary_module.SecondaryProvider.trains_on_input = original  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
