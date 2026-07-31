"""Provider abstraction tests — privacy enforcement and protocol conformance."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
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


# ---------------------------------------------------------------------------
# SecondaryProvider.complete — real OpenRouter implementation
# ---------------------------------------------------------------------------


def _mock_openrouter_response(
    *, content: str = '{"ok": true}', provider: str = "DeepInfra"
) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(
        return_value={
            "provider": provider,
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4},
        }
    )
    return resp


@pytest.mark.asyncio
async def test_secondary_provider_complete_success() -> None:
    """A configured SecondaryProvider parses a successful OpenRouter response."""
    provider = SecondaryProvider(api_key="or-key", model="meta-llama/llama-3.3-70b-instruct")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_ctx.post = AsyncMock(return_value=_mock_openrouter_response())
        mock_client_cls.return_value = mock_ctx

        raw, tokens_in, tokens_out = await provider.complete("review text", system_prompt="sys")

    assert raw == '{"ok": true}'
    assert tokens_in == 12
    assert tokens_out == 4


@pytest.mark.asyncio
async def test_secondary_provider_complete_sends_zdr_true() -> None:
    """Every OpenRouter request must set provider.zdr=true — fail-closed privacy enforcement.

    This is the single most important behavioural test in this module: it is what stops
    a future edit from silently dropping the ZDR restriction and routing to a
    training-eligible endpoint.
    """
    provider = SecondaryProvider(api_key="or-key", model="meta-llama/llama-3.3-70b-instruct")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_ctx.post = AsyncMock(return_value=_mock_openrouter_response())
        mock_client_cls.return_value = mock_ctx

        await provider.complete("review text", system_prompt="sys")

        _, kwargs = mock_ctx.post.call_args
        assert kwargs["json"]["provider"] == {"zdr": True}


@pytest.mark.asyncio
async def test_secondary_provider_complete_http_error_propagates() -> None:
    """An OpenRouter HTTP failure (e.g. no ZDR endpoint for the model) raises, not swallows.

    The caller (app.core.llm.extract_with_llm) is responsible for catching this and
    falling through — SecondaryProvider itself must fail loudly.
    """
    provider = SecondaryProvider(api_key="or-key", model="some/unrouted-model")

    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    error_response = httpx.Response(
        404, request=request, json={"error": {"message": "no endpoints"}}
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("404", request=request, response=error_response)
    )

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_ctx.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_ctx

        with pytest.raises(httpx.HTTPStatusError):
            await provider.complete("review text", system_prompt="sys")


@pytest.mark.asyncio
async def test_secondary_provider_complete_trains_on_input_stays_false() -> None:
    """trains_on_input must remain False on the real implementation, not just the stub."""
    provider = SecondaryProvider(api_key="or-key", model="meta-llama/llama-3.3-70b-instruct")
    assert provider.trains_on_input is False
    assert_privacy_safe(provider)  # must not raise


@pytest.mark.asyncio
async def test_secondary_provider_complete_retry_appends_suffix() -> None:
    """retry=True mirrors GroqProvider's behaviour: append the parse-retry suffix."""
    from app.core.providers.groq import _RETRY_SUFFIX

    provider = SecondaryProvider(api_key="or-key", model="meta-llama/llama-3.3-70b-instruct")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_ctx.post = AsyncMock(return_value=_mock_openrouter_response())
        mock_client_cls.return_value = mock_ctx

        await provider.complete("review text", system_prompt="sys", retry=True)

        _, kwargs = mock_ctx.post.call_args
        user_message = kwargs["json"]["messages"][1]["content"]
        assert user_message == "review text" + _RETRY_SUFFIX
