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
    GEMINI_API_KEY="",
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

        result, model, latency_ms, tin, tout = await llm_module.extract_with_llm(
            "test prompt", allow_gemini_fallback=False
        )

    assert result.sentiment == "positive"
    assert "llama" in model


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
            result, model, latency_ms, tin, tout = await llm_module.extract_with_llm(
                "test prompt", allow_gemini_fallback=False
            )

    assert result.sentiment == "positive"
    assert model == "some-secondary-model"


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
                await llm_module.extract_with_llm("test prompt", allow_gemini_fallback=False)


# ---------------------------------------------------------------------------
# No secondary configured → falls through to Gemini (demo path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_groq_fails_no_secondary_uses_gemini_on_demo_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        llm_module,
        "get_settings",
        lambda: _settings(GEMINI_API_KEY="fake-gemini-key"),
    )

    with patch("app.core.providers.groq.AsyncGroq") as mock_groq:
        mock_client = mock_groq.return_value
        mock_client.chat.completions.create = AsyncMock(side_effect=_api_error())

        with patch.object(
            llm_module,
            "_call_gemini",
            new_callable=AsyncMock,
            return_value=(_GOOD_EXTRACTION, 12, 6),
        ):
            result, model, latency_ms, tin, tout = await llm_module.extract_with_llm(
                "test prompt", allow_gemini_fallback=True
            )

    assert result.sentiment == "positive"


# ---------------------------------------------------------------------------
# No secondary, no Gemini, org-key path → RuntimeError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_providers_fail_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_module, "get_settings", lambda: _settings())

    with patch("app.core.providers.groq.AsyncGroq") as mock_groq:
        mock_client = mock_groq.return_value
        mock_client.chat.completions.create = AsyncMock(side_effect=_api_error())

        with pytest.raises(RuntimeError, match="All LLM providers failed"):
            await llm_module.extract_with_llm("test prompt", allow_gemini_fallback=False)


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
                await llm_module.extract_with_llm("test prompt", allow_gemini_fallback=False)
    finally:
        secondary_module.SecondaryProvider.trains_on_input = original  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Gemini never called on org-key path (allow_gemini_fallback=False)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_not_called_on_org_key_path_even_with_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With allow_gemini_fallback=False, Gemini is never invoked even if a key is present."""
    monkeypatch.setattr(
        llm_module,
        "get_settings",
        lambda: _settings(GEMINI_API_KEY="fake-gemini-key"),
    )
    gemini_called = False

    async def forbidden_gemini(_: str) -> None:
        nonlocal gemini_called
        gemini_called = True
        raise AssertionError("Gemini must not be called on org-key path")

    monkeypatch.setattr(llm_module, "_call_gemini", forbidden_gemini)

    with patch("app.core.providers.groq.AsyncGroq") as mock_groq:
        mock_client = mock_groq.return_value
        mock_client.chat.completions.create = AsyncMock(side_effect=_api_error())

        with pytest.raises(RuntimeError):
            await llm_module.extract_with_llm("test prompt", allow_gemini_fallback=False)

    assert not gemini_called, "Gemini was called on the org-key path — privacy violation"
