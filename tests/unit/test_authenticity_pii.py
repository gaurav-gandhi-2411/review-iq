"""The authenticity engine must hand Groq PII-redacted review text (offline, provider mocked).

legal/sub-processors.md says Groq receives text only after the PII-redaction pass. The engine has
no app/ caller today, but if it is wired in, the text it sends must already be redacted.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from app.core.authenticity.engine import _call_authenticity_llm
from app.core.config import Settings
from app.core.prompts.authenticity import build_authenticity_prompt
from eval.authenticity.replay import authenticity_cassette_key

RAW_OK = json.dumps({"score": 0.9, "flags": [], "reasoning": "specific"})
PII_TEXT = (
    "Great phone. Mail me at jane.doe@example.com or call 9876543210, "
    "card 4111 1111 1111 1111 was charged twice."
)
CLEAN_TEXT = "The battery lasts two days and the screen is bright. Would buy again."


def _settings() -> Settings:
    return Settings(
        GROQ_API_KEY="test-key", GROQ_MODEL_LARGE="mock-large", GROQ_MODEL_SMALL="mock-small"
    )


class _Recorder:
    """Stands in for GroqProvider; records every prompt handed to complete()."""

    trains_on_input = False
    calls: list[tuple[str, str | None]] = []

    def __init__(self, **_: Any) -> None:
        pass

    async def complete(self, prompt: str, *, system_prompt: str | None = None) -> Any:
        type(self).calls.append((prompt, system_prompt))
        return RAW_OK, 1, 1


@pytest.fixture
def recorder() -> type[_Recorder]:
    _Recorder.calls = []
    with patch("app.core.authenticity.engine.GroqProvider", _Recorder):
        yield _Recorder


@pytest.mark.asyncio
async def test_provider_receives_redacted_pii(recorder: type[_Recorder]) -> None:
    out = await _call_authenticity_llm(PII_TEXT, "en", _settings())
    assert out[4] is True
    ((prompt, system),) = recorder.calls
    sent = prompt + (system or "")
    for marker in ("[EMAIL]", "[PHONE]", "[CARD]"):
        assert marker in prompt
    for raw in ("jane.doe@example.com", "9876543210", "4111 1111 1111 1111"):
        assert raw not in sent


@pytest.mark.asyncio
async def test_pii_free_prompt_is_unchanged(recorder: type[_Recorder]) -> None:
    await _call_authenticity_llm(CLEAN_TEXT, "en", _settings())
    ((prompt, system),) = recorder.calls
    assert (system, prompt) == build_authenticity_prompt(CLEAN_TEXT, "en")


def test_replay_key_mirrors_engine_redaction() -> None:
    """The eval's cassette key must be computed over the same redacted text the engine sends."""
    from app.core.language import detect_language
    from app.core.providers.groq import _make_cassette_key
    from app.core.sanitize import pii_redacted_text

    system, user = build_authenticity_prompt(pii_redacted_text(PII_TEXT), detect_language(PII_TEXT))
    assert authenticity_cassette_key(PII_TEXT, "m") == _make_cassette_key("m", system, user)
