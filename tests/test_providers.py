"""Provider abstraction tests — privacy enforcement and protocol conformance."""

from __future__ import annotations

import pytest
from app.core.providers.base import Provider, assert_privacy_safe
from app.core.providers.groq import GroqProvider
from app.core.providers.secondary import SecondaryProvider

# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_groq_provider_satisfies_protocol() -> None:
    """GroqProvider satisfies Provider structurally (no inheritance required)."""
    provider = GroqProvider(model="llama-3.3-70b-versatile", api_key="key")
    assert isinstance(provider, Provider)


def test_secondary_provider_satisfies_protocol() -> None:
    provider = SecondaryProvider()
    assert isinstance(provider, Provider)


# ---------------------------------------------------------------------------
# trains_on_input capability flags
# ---------------------------------------------------------------------------


def test_groq_trains_on_input_is_false() -> None:
    provider = GroqProvider(model="llama-3.3-70b-versatile", api_key="key")
    assert provider.trains_on_input is False


def test_secondary_trains_on_input_is_false() -> None:
    provider = SecondaryProvider()
    assert provider.trains_on_input is False


# ---------------------------------------------------------------------------
# assert_privacy_safe
# ---------------------------------------------------------------------------


def test_assert_privacy_safe_passes_groq() -> None:
    provider = GroqProvider(model="llama-3.3-70b-versatile", api_key="key")
    assert_privacy_safe(provider)  # must not raise


def test_assert_privacy_safe_rejects_train_on_input() -> None:
    class TrainOnInputProvider:
        trains_on_input: bool = True

        async def complete(
            self,
            user_prompt: str,
            *,
            system_prompt: str,
            retry: bool = False,
            timeout: int = 30,
        ) -> tuple[str, int, int]:
            return "", 0, 0

    with pytest.raises(RuntimeError, match="trains on input"):
        assert_privacy_safe(TrainOnInputProvider())  # type: ignore[arg-type]


def test_assert_privacy_safe_custom_context_in_message() -> None:
    class BadProvider:
        trains_on_input: bool = True

        async def complete(
            self,
            user_prompt: str,
            *,
            system_prompt: str,
            retry: bool = False,
            timeout: int = 30,
        ) -> tuple[str, int, int]:
            return "", 0, 0

    with pytest.raises(RuntimeError, match="org-key path"):
        assert_privacy_safe(BadProvider())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# org-key path: Gemini never called when allow_gemini_fallback=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_not_called_on_org_key_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """With allow_gemini_fallback=False, _call_gemini is never invoked even on Groq failure."""
    import app.core.llm as llm_module
    from app.core.config import Settings

    gemini_called = False

    async def fake_gemini(user_prompt: str) -> tuple[object, int, int]:
        nonlocal gemini_called
        gemini_called = True
        raise AssertionError("Gemini must not be called on the org-key path")

    monkeypatch.setattr(llm_module, "_call_gemini", fake_gemini)

    # Groq will fail because the fake key is invalid; with fallback disabled it must raise.
    monkeypatch.setattr(
        llm_module,
        "get_settings",
        lambda: Settings(
            GROQ_API_KEY="fake-key",
            GEMINI_API_KEY="fake-key",
            ENABLE_GEMINI_FALLBACK=False,
        ),
    )

    with pytest.raises(Exception):  # RuntimeError or APIError from failed Groq call
        await llm_module.extract_with_llm("test prompt", allow_gemini_fallback=False)

    assert not gemini_called, "Gemini was called on the org-key path — privacy violation"


# ---------------------------------------------------------------------------
# SecondaryProvider stub behaviour
# ---------------------------------------------------------------------------


def test_secondary_provider_unconfigured() -> None:
    provider = SecondaryProvider()
    assert not provider.is_configured


def test_secondary_provider_configured() -> None:
    provider = SecondaryProvider(api_key="key", model="some-model")
    assert provider.is_configured


@pytest.mark.asyncio
async def test_secondary_provider_raises_when_unconfigured() -> None:
    provider = SecondaryProvider()
    with pytest.raises(RuntimeError, match="not configured"):
        await provider.complete("prompt", system_prompt="sys")
