"""Unit tests for scripts/probe_failover.py — the nightly synthetic failover probe.

scripts/ has no __init__.py (matches this repo's existing convention for one-off
scripts), so the module is imported by inserting its directory onto sys.path rather
than as a package.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from app.core.config import Settings
from app.core.schemas import ReviewExtractionLLMOutput

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import probe_failover  # noqa: E402 -- must follow the sys.path insert above

_GOOD_EXTRACTION = ReviewExtractionLLMOutput(
    sentiment="positive",
    stars=None,
    buy_again=True,
    pros=["good sound"],
    cons=[],
    topics=["audio"],
    language="en",
    confidence=0.9,
)
_GOOD_RAW = _GOOD_EXTRACTION.model_dump_json()


def _settings(**overrides: object) -> Settings:
    base = dict(
        GEMINI_API_KEY="",
        SECONDARY_PROVIDER_API_KEY="",
        SECONDARY_PROVIDER_MODEL="",
    )
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# probe_gemini
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_gemini_unconfigured_fails_fast() -> None:
    with patch("app.core.config.get_settings", lambda: _settings()):
        result = await probe_failover.probe_gemini()
    assert result.ok is False
    assert "GEMINI_API_KEY" in result.detail


@pytest.mark.asyncio
async def test_probe_gemini_success() -> None:
    with patch("app.core.config.get_settings", lambda: _settings(GEMINI_API_KEY="fake-key")):
        with patch(
            "app.core.llm._call_gemini",
            new_callable=AsyncMock,
            return_value=(_GOOD_EXTRACTION, 10, 5),
        ):
            result = await probe_failover.probe_gemini()
    assert result.ok is True
    assert "gemini-2.5-flash" in result.detail


@pytest.mark.asyncio
async def test_probe_gemini_call_failure_reported() -> None:
    with patch("app.core.config.get_settings", lambda: _settings(GEMINI_API_KEY="fake-key")):
        with patch(
            "app.core.llm._call_gemini",
            new_callable=AsyncMock,
            side_effect=RuntimeError("quota exhausted"),
        ):
            result = await probe_failover.probe_gemini()
    assert result.ok is False
    assert "quota exhausted" in result.detail


# ---------------------------------------------------------------------------
# probe_secondary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_secondary_unconfigured_fails_fast() -> None:
    with patch("app.core.config.get_settings", lambda: _settings()):
        result = await probe_failover.probe_secondary()
    assert result.ok is False
    assert "SECONDARY_PROVIDER" in result.detail


@pytest.mark.asyncio
async def test_probe_secondary_success() -> None:
    settings = _settings(
        SECONDARY_PROVIDER_API_KEY="fake-or-key",
        SECONDARY_PROVIDER_MODEL="meta-llama/llama-3.3-70b-instruct",
    )
    with patch("app.core.config.get_settings", lambda: settings):
        with patch(
            "app.core.providers.secondary.SecondaryProvider.complete",
            new_callable=AsyncMock,
            return_value=(_GOOD_RAW, 8, 4),
        ):
            result = await probe_failover.probe_secondary()
    assert result.ok is True
    assert "meta-llama/llama-3.3-70b-instruct" in result.detail


@pytest.mark.asyncio
async def test_probe_secondary_http_failure_reported() -> None:
    settings = _settings(
        SECONDARY_PROVIDER_API_KEY="fake-or-key",
        SECONDARY_PROVIDER_MODEL="some/unrouted-model",
    )
    with patch("app.core.config.get_settings", lambda: settings):
        with patch(
            "app.core.providers.secondary.SecondaryProvider.complete",
            new_callable=AsyncMock,
            side_effect=RuntimeError("404 No endpoints found"),
        ):
            result = await probe_failover.probe_secondary()
    assert result.ok is False
    assert "404" in result.detail


# ---------------------------------------------------------------------------
# main() exit code — the actual alerting mechanism
# ---------------------------------------------------------------------------


def test_main_exits_zero_when_both_paths_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_probe() -> list[probe_failover.ProbeResult]:
        return [
            probe_failover.ProbeResult("gemini", True, 100, "ok"),
            probe_failover.ProbeResult("secondary", True, 100, "ok"),
        ]

    monkeypatch.setattr(probe_failover, "run_probe", fake_run_probe)
    monkeypatch.setattr(sys, "argv", ["probe_failover.py"])
    assert probe_failover.main() == 0


def test_main_exits_nonzero_and_names_failed_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_run_probe() -> list[probe_failover.ProbeResult]:
        return [
            probe_failover.ProbeResult("gemini", True, 100, "ok"),
            probe_failover.ProbeResult("secondary", False, 50, "boom"),
        ]

    monkeypatch.setattr(probe_failover, "run_probe", fake_run_probe)
    monkeypatch.setattr(sys, "argv", ["probe_failover.py"])
    exit_code = probe_failover.main()
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "secondary" in captured.out
    assert "boom" in captured.out
