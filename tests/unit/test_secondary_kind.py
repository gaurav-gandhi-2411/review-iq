from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.core.config import Settings
from app.core.llm import _build_secondary
from app.core.providers.groq import GroqProvider
from app.core.providers.secondary import SecondaryProvider
from pydantic import ValidationError


def _settings(kind: str) -> SimpleNamespace:
    return SimpleNamespace(
        secondary_provider_kind=kind,
        secondary_provider_api_key="k",
        secondary_provider_model="openai/gpt-oss-20b",
        llm_timeout_seconds=30,
    )


def test_kind_has_no_default() -> None:
    # S23: no silent OpenRouter default.
    assert Settings.model_fields["secondary_provider_kind"].default == ""


def test_openrouter_kind_builds_the_zdr_provider() -> None:
    assert isinstance(_build_secondary(_settings("openrouter")), SecondaryProvider)


@pytest.mark.parametrize("kind", ["", "grok"])
def test_build_secondary_rejects_unset_or_unknown_kind(kind: str) -> None:
    with pytest.raises(ValueError, match="SECONDARY_PROVIDER_KIND.*groq, openrouter"):
        _build_secondary(_settings(kind))


def _make(**env: str) -> Settings:
    return Settings(_env_file=None, **env)  # type: ignore[call-arg,arg-type]


def test_unset_kind_with_key_configured_fails_naming_variable_and_allowed_values() -> None:
    with pytest.raises(ValidationError) as exc:
        _make(SECONDARY_PROVIDER_API_KEY="SENTINEL-KEY", SECONDARY_PROVIDER_MODEL="m")
    msg = str(exc.value)
    assert "SECONDARY_PROVIDER_KIND" in msg
    assert "groq, openrouter" in msg
    assert "SENTINEL-KEY" not in msg  # the error must never echo the key


def test_unset_kind_with_model_only_configured_also_fails() -> None:
    with pytest.raises(ValidationError, match="SECONDARY_PROVIDER_KIND"):
        _make(SECONDARY_PROVIDER_MODEL="m")


def test_unset_kind_with_nothing_configured_is_fine() -> None:
    assert _make().secondary_provider_kind == ""


def test_invalid_kind_fails_even_with_nothing_configured() -> None:
    with pytest.raises(ValidationError, match="groq, openrouter"):
        _make(SECONDARY_PROVIDER_KIND="grok")


@pytest.mark.parametrize("kind", ["groq", "openrouter", " Groq "])
def test_valid_kinds_accepted_and_normalised(kind: str) -> None:
    s = _make(
        SECONDARY_PROVIDER_KIND=kind, SECONDARY_PROVIDER_API_KEY="k", SECONDARY_PROVIDER_MODEL="m"
    )
    assert s.secondary_provider_kind == kind.strip().lower()


def test_groq_kind_builds_a_second_groq_account_provider() -> None:
    provider = _build_secondary(_settings("groq"))
    assert isinstance(provider, GroqProvider)
    assert provider.model == "openai/gpt-oss-20b"
    assert provider.trains_on_input is False
