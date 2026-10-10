from __future__ import annotations

from types import SimpleNamespace

from app.core.config import Settings
from app.core.llm import _build_secondary
from app.core.providers.groq import GroqProvider
from app.core.providers.secondary import SecondaryProvider


def _settings(kind: str) -> SimpleNamespace:
    return SimpleNamespace(
        secondary_provider_kind=kind,
        secondary_provider_api_key="k",
        secondary_provider_model="openai/gpt-oss-20b",
        llm_timeout_seconds=30,
    )


def test_default_kind_is_openrouter() -> None:
    assert Settings.model_fields["secondary_provider_kind"].default == "openrouter"
    assert isinstance(_build_secondary(_settings("openrouter")), SecondaryProvider)


def test_unknown_kind_falls_back_to_the_zdr_openrouter_path() -> None:
    # A typo must never silently select a different (non-ZDR) backend.
    assert isinstance(_build_secondary(_settings("grok")), SecondaryProvider)


def test_groq_kind_builds_a_second_groq_account_provider() -> None:
    provider = _build_secondary(_settings("groq"))
    assert isinstance(provider, GroqProvider)
    assert provider.model == "openai/gpt-oss-20b"
    assert provider.trains_on_input is False
