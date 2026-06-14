"""Router integration tests — tier selection, escalation, model routing."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import app.core.router as router_module  # noqa: E402
import pytest
from app.core.config import Settings
from app.core.routing_policy import CONFIDENCE_ESCALATION_THRESHOLD
from app.core.schemas import ReviewExtractionLLMOutput

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GOOD_HIGH_CONF = ReviewExtractionLLMOutput(
    sentiment="positive",
    stars=None,
    buy_again=True,
    pros=["good quality"],
    cons=[],
    topics=["quality"],
    language="en",
    confidence=0.95,
)
_GOOD_LOW_CONF = ReviewExtractionLLMOutput(
    sentiment="positive",
    stars=None,
    buy_again=True,
    pros=["ok"],
    cons=[],
    topics=["quality"],
    language="en",
    confidence=CONFIDENCE_ESCALATION_THRESHOLD - 0.1,
)
_GOOD_RAW_HIGH = json.dumps(_GOOD_HIGH_CONF.model_dump())
_GOOD_RAW_LOW = json.dumps(_GOOD_LOW_CONF.model_dump())


def _settings(**overrides: object) -> Settings:
    base = dict(
        GROQ_API_KEY="fake-key",
        GROQ_MODEL_SMALL="llama-3.1-8b-instant",
        GROQ_MODEL_LARGE="llama-3.3-70b-versatile",
        LLM_MAX_RETRIES=0,
        LLM_TIMEOUT_SECONDS=30,
    )
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


_SYSTEM = "system prompt"


# ---------------------------------------------------------------------------
# hi-en → small model first (carried-debt fix: all languages start small now)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hi_en_routes_to_small_model_first() -> None:
    """hi-en now starts on small (carried-debt fix); high-confidence → no escalation."""
    # A prompt with a strong Hinglish marker triggers hi-en detection.
    prompt = "ekdum sahi product hai, bahut achha hai"
    settings = _settings()

    with patch("app.core.router.GroqProvider") as MockProvider:
        instance = MockProvider.return_value
        instance.model = settings.groq_model_small
        instance.trains_on_input = False
        # High confidence → no escalation; small model result is accepted.
        instance.complete = AsyncMock(return_value=(_GOOD_RAW_HIGH, 10, 5))

        extraction, model, tin, tout, escalated, degraded = await router_module.route_extraction(
            prompt, _SYSTEM, allow_gemini_fallback=False, settings=settings
        )

    assert not escalated
    assert not degraded
    assert extraction.confidence == 0.95


# ---------------------------------------------------------------------------
# en → small model, no escalation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_en_routes_to_small_no_escalation() -> None:
    """English review with high confidence → small model, no escalation."""
    prompt = "This product is absolutely fantastic and works great every time."
    settings = _settings()

    with patch("app.core.router.GroqProvider") as MockProvider:
        # small provider
        small_instance = MockProvider.return_value
        small_instance.model = settings.groq_model_small
        small_instance.trains_on_input = False
        small_instance.complete = AsyncMock(return_value=(_GOOD_RAW_HIGH, 8, 4))

        extraction, model, tin, tout, escalated, degraded = await router_module.route_extraction(
            prompt, _SYSTEM, allow_gemini_fallback=False, settings=settings
        )

    assert not escalated
    assert not degraded
    assert extraction.sentiment == "positive"


# ---------------------------------------------------------------------------
# en → small model → low confidence → escalates to large
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_en_low_confidence_escalates_to_large() -> None:
    """Low confidence from small model triggers escalation to large."""
    prompt = "This product is absolutely fantastic and works great every time."
    settings = _settings()

    # small returns low confidence; large returns high confidence
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
            return _GOOD_RAW_LOW, 8, 4  # small: low confidence
        return _GOOD_RAW_HIGH, 12, 6  # large: high confidence

    with patch("app.core.router.GroqProvider") as MockProvider:
        instance = MockProvider.return_value
        instance.model = settings.groq_model_small
        instance.trains_on_input = False
        instance.complete = fake_complete

        extraction, model, tin, tout, escalated, degraded = await router_module.route_extraction(
            prompt, _SYSTEM, allow_gemini_fallback=False, settings=settings
        )

    assert escalated
    assert not degraded
    assert call_count == 2  # small + large


# ---------------------------------------------------------------------------
# en → small model → schema fail → escalates to large
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_en_schema_fail_escalates_to_large() -> None:
    """Unparseable small-model output triggers schema_validation_failed escalation."""
    prompt = "This product is absolutely fantastic."
    settings = _settings()

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
            return "not valid json {{{{", 8, 4  # small: bad JSON
        return _GOOD_RAW_HIGH, 12, 6  # large: good

    with patch("app.core.router.GroqProvider") as MockProvider:
        instance = MockProvider.return_value
        instance.model = settings.groq_model_small
        instance.trains_on_input = False
        instance.complete = fake_complete

        extraction, model, tin, tout, escalated, degraded = await router_module.route_extraction(
            prompt, _SYSTEM, allow_gemini_fallback=False, settings=settings
        )

    assert escalated
    assert not degraded
    assert extraction.sentiment == "positive"


# ---------------------------------------------------------------------------
# routing OFF → extract_with_llm behaves identically to v0.4.0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_routing_off_uses_groq_model_directly() -> None:
    """With enable_tiered_routing=False, extract_with_llm uses groq_model (not small/large)."""
    import app.core.llm as llm_module

    settings = Settings(
        GROQ_API_KEY="fake-key",
        ENABLE_TIERED_ROUTING=False,
        GROQ_MODEL="llama-3.3-70b-versatile",
        LLM_MAX_RETRIES=0,
    )

    with patch.object(llm_module, "get_settings", return_value=settings):
        with patch("app.core.providers.groq.AsyncGroq") as MockGroq:
            mock_client = MockGroq.return_value
            mock_resp = AsyncMock()
            mock_resp.choices = [AsyncMock()]
            mock_resp.choices[0].message.content = _GOOD_RAW_HIGH
            mock_resp.usage.prompt_tokens = 10
            mock_resp.usage.completion_tokens = 5
            mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

            result, model, latency_ms, tin, tout, degraded = await llm_module.extract_with_llm(
                "test prompt", allow_gemini_fallback=False
            )

    assert model == "llama-3.3-70b-versatile"
    assert not degraded
    assert result.sentiment == "positive"


# ---------------------------------------------------------------------------
# routing ON → extract_with_llm delegates to router
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_routing_on_delegates_to_route_extraction() -> None:
    """With enable_tiered_routing=True, extract_with_llm calls route_extraction."""
    import app.core.llm as llm_module

    settings = Settings(
        GROQ_API_KEY="fake-key",
        ENABLE_TIERED_ROUTING=True,
        GROQ_MODEL_SMALL="llama-3.1-8b-instant",
        GROQ_MODEL_LARGE="llama-3.3-70b-versatile",
        LLM_MAX_RETRIES=0,
    )
    router_called = False

    async def fake_route(
        user_prompt: str,
        system_prompt: str,
        *,
        allow_gemini_fallback: bool,
        settings: Settings,
    ) -> tuple[object, str, int, int, bool, bool]:
        nonlocal router_called
        router_called = True
        return _GOOD_HIGH_CONF, "llama-3.3-70b-versatile", 10, 5, False, False

    with patch.object(llm_module, "get_settings", return_value=settings):
        with patch.object(llm_module, "route_extraction", fake_route):
            result, model, latency_ms, tin, tout, degraded = await llm_module.extract_with_llm(
                "test prompt", allow_gemini_fallback=False
            )

    assert router_called
    assert result.sentiment == "positive"
