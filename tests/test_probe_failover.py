"""Unit tests for scripts/probe_failover.py — the nightly synthetic failover probe.

scripts/ has no __init__.py (matches this repo's existing convention for one-off
scripts), so the module is imported by inserting its directory onto sys.path rather
than as a package.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import structlog
from app.core.config import Settings
from app.core.schemas import ReviewExtractionLLMOutput

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import probe_failover  # noqa: E402 -- must follow the sys.path insert above

PASS = probe_failover.ProbeState.PASS
FAIL = probe_failover.ProbeState.FAIL
NOT_CONFIGURED = probe_failover.ProbeState.NOT_CONFIGURED
ACK = probe_failover.ACK_ENV_VAR


@pytest.fixture(autouse=True)
def _isolate_github_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() appends to $GITHUB_STEP_SUMMARY/$GITHUB_OUTPUT: never let tests touch CI's real ones."""
    for var in ("GITHUB_STEP_SUMMARY", "GITHUB_OUTPUT", ACK):
        monkeypatch.delenv(var, raising=False)


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
        SECONDARY_PROVIDER_API_KEY="",
        SECONDARY_PROVIDER_MODEL="",
        # Explicit: an unset kind with a key configured is a startup error (S23).
        SECONDARY_PROVIDER_KIND="openrouter",
    )
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# probe_secondary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_secondary_unconfigured_fails_fast() -> None:
    with patch("app.core.config.get_settings", lambda: _settings()):
        result = await probe_failover.probe_secondary()
    assert result.ok is False
    assert result.state is NOT_CONFIGURED
    assert "SECONDARY_PROVIDER" in result.detail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "missing"),
    [
        ({"SECONDARY_PROVIDER_API_KEY": "fake-or-key"}, "SECONDARY_PROVIDER_MODEL"),
        ({"SECONDARY_PROVIDER_MODEL": "some/model"}, "SECONDARY_PROVIDER_API_KEY"),
    ],
)
async def test_probe_secondary_partial_config_is_fail_not_unconfigured(
    overrides: dict[str, str], missing: str
) -> None:
    """Half-configured is a broken config someone attempted, never an 'absent' path."""
    with patch("app.core.config.get_settings", lambda: _settings(**overrides)):
        result = await probe_failover.probe_secondary()
    assert result.state is FAIL
    assert missing in result.detail


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
    assert result.state is PASS
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
    assert result.state is FAIL
    assert "404" in result.detail


# ---------------------------------------------------------------------------
# main() exit code — the actual alerting mechanism
# ---------------------------------------------------------------------------


_OTHER_UNCONFIGURED = "OTHER_API_KEY absent"
_SECONDARY_UNCONFIGURED = "SECONDARY_PROVIDER_API_KEY / SECONDARY_PROVIDER_MODEL absent"


def _res(
    path: str, state: probe_failover.ProbeState, detail: str = "d"
) -> probe_failover.ProbeResult:
    return probe_failover.ProbeResult(path, state, 0 if state is not PASS else 100, detail)


def _run_main(
    monkeypatch: pytest.MonkeyPatch,
    results: list[probe_failover.ProbeResult],
    *,
    ack: str | None = None,
) -> int:
    """Run main() with run_probe() stubbed (no network) and the ack env var set as given."""

    async def fake_run_probe() -> list[probe_failover.ProbeResult]:
        return results

    monkeypatch.setattr(probe_failover, "run_probe", fake_run_probe)
    monkeypatch.setattr(sys, "argv", ["probe_failover.py"])
    if ack is not None:
        monkeypatch.setenv(ACK, ack)
    return probe_failover.main()


def test_main_exits_zero_when_both_paths_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run_main(monkeypatch, [_res("other", PASS), _res("secondary", PASS)]) == 0


def test_main_exits_nonzero_and_names_failed_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = _run_main(monkeypatch, [_res("other", PASS), _res("secondary", FAIL, "boom")])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "[FAIL] secondary" in captured.out
    assert "boom" in captured.out


# ---------------------------------------------------------------------------
# Session 15c (D4): three-state result, visible NOT_CONFIGURED, acknowledgement rule
# ---------------------------------------------------------------------------


def test_unacknowledged_unconfigured_secondary_exits_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = [_res("other", PASS), _res("secondary", NOT_CONFIGURED, _SECONDARY_UNCONFIGURED)]
    assert _run_main(monkeypatch, results) == 1


def test_acknowledged_unconfigured_secondary_exits_zero_but_stays_loud(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    results = [_res("other", PASS), _res("secondary", NOT_CONFIGURED, _SECONDARY_UNCONFIGURED)]

    assert _run_main(monkeypatch, results, ack="secondary") == 0

    out = capsys.readouterr().out
    assert (
        "[NOT CONFIGURED] secondary  -- SECONDARY_PROVIDER_API_KEY / SECONDARY_PROVIDER_MODEL "
        "absent: this failover path is UNPROTECTED and untested"
    ) in out
    assert "::warning title=Failover path UNPROTECTED::secondary  -- " in out
    assert "UNPROTECTED and untested" in summary.read_text(encoding="utf-8")


def test_unconfigured_other_unacknowledged_fails_even_if_secondary_acknowledged(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unacknowledged unconfigured path still fails loudly."""
    results = [
        _res("other", NOT_CONFIGURED, _OTHER_UNCONFIGURED),
        _res("secondary", NOT_CONFIGURED, _SECONDARY_UNCONFIGURED),
    ]
    assert _run_main(monkeypatch, results, ack="secondary") == 1
    out = capsys.readouterr().out
    assert "other: NOT CONFIGURED (NOT acknowledged)" in out
    assert "secondary: NOT CONFIGURED" not in out  # acknowledged one is not a blocker


def test_hostile_both_unconfigured_only_one_acknowledged_exits_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = [
        _res("other", NOT_CONFIGURED, _OTHER_UNCONFIGURED),
        _res("secondary", NOT_CONFIGURED, _SECONDARY_UNCONFIGURED),
    ]
    assert _run_main(monkeypatch, results, ack="secondary") == 1
    assert _run_main(monkeypatch, results, ack="other") == 1


def test_both_unconfigured_both_acknowledged_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    results = [
        _res("other", NOT_CONFIGURED, _OTHER_UNCONFIGURED),
        _res("secondary", NOT_CONFIGURED, _SECONDARY_UNCONFIGURED),
    ]
    assert _run_main(monkeypatch, results, ack=" Other , SECONDARY ") == 0  # case/space tolerant


def test_acknowledgement_never_masks_a_configured_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    results = [_res("other", FAIL, "quota exhausted"), _res("secondary", PASS)]
    assert _run_main(monkeypatch, results, ack="other,secondary") == 1


def test_unknown_acknowledgement_name_is_flagged_and_covers_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    results = [_res("other", PASS), _res("secondary", NOT_CONFIGURED, _SECONDARY_UNCONFIGURED)]
    assert _run_main(monkeypatch, results, ack="secondry") == 1  # typo
    out = capsys.readouterr().out
    assert "::warning title=Unknown failover acknowledgement::" in out
    assert "secondry" in out


def test_not_configured_never_prints_as_pass(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    results = [_res("other", PASS), _res("secondary", NOT_CONFIGURED, _SECONDARY_UNCONFIGURED)]
    _run_main(monkeypatch, results, ack="secondary")

    out = capsys.readouterr().out
    secondary_lines = [ln for ln in out.splitlines() if "secondary" in ln]
    assert secondary_lines, "secondary must be reported on every run (no silent skip)"
    assert not any("[PASS]" in ln or "[OK]" in ln for ln in secondary_lines)
    assert "All failover paths OK." not in out
    row = next(ln for ln in summary.read_text(encoding="utf-8").splitlines() if "| secondary" in ln)
    assert "PASS" not in row
    assert "NOT CONFIGURED (acknowledged)" in row
    assert results[1].ok is False


def test_step_summary_is_a_table_with_one_row_per_path_and_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    summary = tmp_path / "summary.md"
    summary.write_text("earlier step output\n", encoding="utf-8")  # must append, not clobber
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    results = [
        _res("other", FAIL, "bad | pipe\nnewline"),
        _res("secondary", NOT_CONFIGURED, _SECONDARY_UNCONFIGURED),
    ]
    _run_main(monkeypatch, results)  # no acknowledgement

    text = summary.read_text(encoding="utf-8")
    assert text.startswith("earlier step output\n")
    assert "| Path | State | Latency | Detail |" in text
    assert "| other | FAIL | 0ms | bad \\| pipe newline |" in text
    assert "| secondary | NOT CONFIGURED (NOT acknowledged) |" in text
    assert "Exit code: 1" in text


def test_machine_readable_summary_line_and_github_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    gh_output = tmp_path / "output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_output))
    results = [_res("other", PASS), _res("secondary", NOT_CONFIGURED, _SECONDARY_UNCONFIGURED)]
    _run_main(monkeypatch, results, ack="secondary")

    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith("PROBE_SUMMARY ")
    payload = json.loads(last.removeprefix("PROBE_SUMMARY "))
    assert payload == {
        "paths": {"other": "PASS", "secondary": "NOT_CONFIGURED"},
        "acknowledged": ["secondary"],
        "exit_code": 0,
    }
    assert gh_output.read_text(encoding="utf-8") == (
        "states=other=PASS, secondary=NOT CONFIGURED (acknowledged)\n"
    )


def test_annotation_message_escapes_newlines() -> None:
    assert probe_failover._escape_annotation("a\nb%c\r") == "a%0Ab%25c%0D"


def test_end_to_end_target_state_with_mocked_network(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Secondary deliberately unset+acknowledged: real run_probe, no network, exit 0, still loud."""
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv(ACK, "secondary")
    monkeypatch.setattr(sys, "argv", ["probe_failover.py"])
    with patch("app.core.config.get_settings", lambda: _settings()):
        exit_code = probe_failover.main()

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "[NOT CONFIGURED] secondary" in out
    assert "| secondary | NOT CONFIGURED (acknowledged) |" in summary.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# S23: the probe must exercise the provider production builds (no decorative control)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["groq", "openrouter"])
async def test_probe_uses_the_provider_production_builds_for_the_same_settings(kind: str) -> None:
    """The provider the probe calls == what app.core.llm builds for these settings. No network."""
    from app.core.llm import _build_secondary

    model = "openai/gpt-oss-120b" if kind == "groq" else "meta-llama/llama-3.3-70b-instruct"
    settings = _settings(
        SECONDARY_PROVIDER_KIND=kind,
        SECONDARY_PROVIDER_API_KEY="fake-key",
        SECONDARY_PROVIDER_MODEL=model,
    )
    expected = _build_secondary(settings)
    seen: list[object] = []

    async def _capture(self: object, *_a: object, **_k: object) -> tuple[str, int, int]:
        seen.append(self)
        return _GOOD_RAW, 8, 4

    with (
        patch("app.core.config.get_settings", lambda: settings),
        patch.object(type(expected), "complete", _capture),
    ):
        result = await probe_failover.probe_secondary()

    assert result.state is PASS, result.detail
    assert len(seen) == 1
    assert type(seen[0]) is type(expected)
    assert getattr(seen[0], "_model", None) == getattr(expected, "_model", None) == model
    assert getattr(seen[0], "_api_key", None) == getattr(expected, "_api_key", None) == "fake-key"
    assert f"kind={kind}" in result.detail
    assert "fake-key" not in result.detail


def test_groq_kind_probe_provider_is_not_openrouter() -> None:
    """Regression for the S23 finding: KIND=groq must never probe the OpenRouter class."""
    from app.core.providers.groq import GroqProvider
    from app.core.providers.secondary import SecondaryProvider

    settings = _settings(
        SECONDARY_PROVIDER_KIND="groq",
        SECONDARY_PROVIDER_API_KEY="fake-key",
        SECONDARY_PROVIDER_MODEL="openai/gpt-oss-120b",
    )
    from app.core.llm import _build_secondary

    provider = _build_secondary(settings)
    assert isinstance(provider, GroqProvider)
    assert not isinstance(provider, SecondaryProvider)


@pytest.mark.asyncio
async def test_probe_failure_detail_never_contains_the_key() -> None:
    settings = _settings(
        SECONDARY_PROVIDER_KIND="groq",
        SECONDARY_PROVIDER_API_KEY="sekrit-key-123",
        SECONDARY_PROVIDER_MODEL="openai/gpt-oss-120b",
    )
    with (
        patch("app.core.config.get_settings", lambda: settings),
        patch(
            "app.core.providers.groq.GroqProvider.complete",
            new_callable=AsyncMock,
            side_effect=RuntimeError("401 bad key sekrit-key-123"),
        ),
    ):
        result = await probe_failover.probe_secondary()
    assert result.state is FAIL
    assert "sekrit-key-123" not in result.detail


# ---------------------------------------------------------------------------
# probe_failover (forced primary failure through the production extract path)
# ---------------------------------------------------------------------------

_GROQ_SECONDARY = dict(
    SECONDARY_PROVIDER_KIND="groq",
    SECONDARY_PROVIDER_API_KEY="sekrit-key-123",
    SECONDARY_PROVIDER_MODEL="openai/gpt-oss-120b",
)


def _extract_result(model: str) -> tuple[ReviewExtractionLLMOutput, str, int, int, int, bool]:
    return _GOOD_EXTRACTION, model, 10, 2000, 100, False


@pytest.mark.asyncio
async def test_probe_failover_passes_only_when_secondary_serves_and_primary_is_invalid() -> None:
    settings = _settings(GROQ_API_KEY="real-looking-primary", **_GROQ_SECONDARY)
    seen: list[str] = []

    async def _fake_extract(*_a: object, **_k: object) -> tuple:
        seen.append(settings.groq_api_key)  # what the production path would see as primary
        structlog.get_logger("t").warning("llm.failover", outcome="success")
        return _extract_result("openai/gpt-oss-120b")

    with (
        patch("app.core.config.get_settings", lambda: settings),
        patch("app.core.llm.extract_with_llm", _fake_extract),
    ):
        result = await probe_failover.probe_failover()
    assert result.state is PASS, result.detail
    assert seen == [probe_failover._INVALID_PRIMARY_KEY]  # real primary key never reaches it
    assert "real-looking-primary" not in result.detail


@pytest.mark.asyncio
async def test_probe_failover_fails_when_no_failover_event_was_recorded() -> None:
    settings = _settings(**_GROQ_SECONDARY)
    with (
        patch("app.core.config.get_settings", lambda: settings),
        patch(
            "app.core.llm.extract_with_llm",
            AsyncMock(return_value=_extract_result(settings.secondary_provider_model)),
        ),
    ):
        result = await probe_failover.probe_failover()
    assert result.state is FAIL


@pytest.mark.asyncio
async def test_probe_failover_fails_loudly_and_masks_key_when_all_providers_fail() -> None:
    settings = _settings(**_GROQ_SECONDARY)
    with (
        patch("app.core.config.get_settings", lambda: settings),
        patch(
            "app.core.llm.extract_with_llm",
            AsyncMock(side_effect=RuntimeError("401 sekrit-key-123 rejected")),
        ),
    ):
        result = await probe_failover.probe_failover()
    assert result.state is FAIL
    assert "sekrit-key-123" not in result.detail


@pytest.mark.asyncio
async def test_probe_failover_unconfigured_is_not_pass() -> None:
    settings = _settings()
    with patch("app.core.config.get_settings", lambda: settings):
        result = await probe_failover.probe_failover()
    assert result.state is NOT_CONFIGURED


@pytest.mark.asyncio
async def test_probe_paths_env_selects_probes_and_unknown_name_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ok = probe_failover.ProbeResult("x", PASS, 1, "ok")
    monkeypatch.setattr(probe_failover, "probe_failover", AsyncMock(return_value=ok))
    monkeypatch.setattr(probe_failover, "probe_secondary", AsyncMock(return_value=ok))
    monkeypatch.setenv("PROBE_PATHS", "failover")
    assert [r.state for r in await probe_failover.run_probe()] == [PASS]
    monkeypatch.setenv("PROBE_PATHS", "failover,bogus")
    assert [r.state for r in await probe_failover.run_probe()] == [PASS, FAIL]


def test_blocking_failure_writes_marker_to_stderr_for_log_alerting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = probe_failover.ProbeResult("failover", FAIL, 5, "boom")
    monkeypatch.setattr(probe_failover, "run_probe", AsyncMock(return_value=[bad]))
    monkeypatch.setattr(sys, "argv", ["probe_failover.py"])
    assert probe_failover.main() == 1
    assert "FAILOVER_PROBE_FAILED failover=FAIL" in capsys.readouterr().err
