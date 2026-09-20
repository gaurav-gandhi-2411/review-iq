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
    assert result.state is NOT_CONFIGURED
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
    assert result.state is PASS
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
    assert result.state is FAIL
    assert "quota exhausted" in result.detail


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


_GEMINI_UNCONFIGURED = "GEMINI_API_KEY absent"
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
    assert _run_main(monkeypatch, [_res("gemini", PASS), _res("secondary", PASS)]) == 0


def test_main_exits_nonzero_and_names_failed_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = _run_main(monkeypatch, [_res("gemini", PASS), _res("secondary", FAIL, "boom")])
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
    results = [_res("gemini", PASS), _res("secondary", NOT_CONFIGURED, _SECONDARY_UNCONFIGURED)]
    assert _run_main(monkeypatch, results) == 1


def test_acknowledged_unconfigured_secondary_exits_zero_but_stays_loud(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    results = [_res("gemini", PASS), _res("secondary", NOT_CONFIGURED, _SECONDARY_UNCONFIGURED)]

    assert _run_main(monkeypatch, results, ack="secondary") == 0

    out = capsys.readouterr().out
    assert (
        "[NOT CONFIGURED] secondary  -- SECONDARY_PROVIDER_API_KEY / SECONDARY_PROVIDER_MODEL "
        "absent: this failover path is UNPROTECTED and untested"
    ) in out
    assert "::warning title=Failover path UNPROTECTED::secondary  -- " in out
    assert "UNPROTECTED and untested" in summary.read_text(encoding="utf-8")


def test_unconfigured_gemini_unacknowledged_fails_even_if_secondary_acknowledged(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Today's real state (no GEMINI_API_KEY yet): the primary path still fails loudly."""
    results = [
        _res("gemini", NOT_CONFIGURED, _GEMINI_UNCONFIGURED),
        _res("secondary", NOT_CONFIGURED, _SECONDARY_UNCONFIGURED),
    ]
    assert _run_main(monkeypatch, results, ack="secondary") == 1
    out = capsys.readouterr().out
    assert "gemini: NOT CONFIGURED (NOT acknowledged)" in out
    assert "secondary: NOT CONFIGURED" not in out  # acknowledged one is not a blocker


def test_hostile_both_unconfigured_only_one_acknowledged_exits_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = [
        _res("gemini", NOT_CONFIGURED, _GEMINI_UNCONFIGURED),
        _res("secondary", NOT_CONFIGURED, _SECONDARY_UNCONFIGURED),
    ]
    assert _run_main(monkeypatch, results, ack="secondary") == 1
    assert _run_main(monkeypatch, results, ack="gemini") == 1


def test_both_unconfigured_both_acknowledged_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    results = [
        _res("gemini", NOT_CONFIGURED, _GEMINI_UNCONFIGURED),
        _res("secondary", NOT_CONFIGURED, _SECONDARY_UNCONFIGURED),
    ]
    assert _run_main(monkeypatch, results, ack=" Gemini , SECONDARY ") == 0  # case/space tolerant


def test_acknowledgement_never_masks_a_configured_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    results = [_res("gemini", FAIL, "quota exhausted"), _res("secondary", PASS)]
    assert _run_main(monkeypatch, results, ack="gemini,secondary") == 1


def test_unknown_acknowledgement_name_is_flagged_and_covers_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    results = [_res("gemini", PASS), _res("secondary", NOT_CONFIGURED, _SECONDARY_UNCONFIGURED)]
    assert _run_main(monkeypatch, results, ack="secondry") == 1  # typo
    out = capsys.readouterr().out
    assert "::warning title=Unknown failover acknowledgement::" in out
    assert "secondry" in out


def test_not_configured_never_prints_as_pass(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    results = [_res("gemini", PASS), _res("secondary", NOT_CONFIGURED, _SECONDARY_UNCONFIGURED)]
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
        _res("gemini", FAIL, "bad | pipe\nnewline"),
        _res("secondary", NOT_CONFIGURED, _SECONDARY_UNCONFIGURED),
    ]
    _run_main(monkeypatch, results)  # no acknowledgement

    text = summary.read_text(encoding="utf-8")
    assert text.startswith("earlier step output\n")
    assert "| Path | State | Latency | Detail |" in text
    assert "| gemini | FAIL | 0ms | bad \\| pipe newline |" in text
    assert "| secondary | NOT CONFIGURED (NOT acknowledged) |" in text
    assert "Exit code: 1" in text


def test_machine_readable_summary_line_and_github_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    gh_output = tmp_path / "output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_output))
    results = [_res("gemini", PASS), _res("secondary", NOT_CONFIGURED, _SECONDARY_UNCONFIGURED)]
    _run_main(monkeypatch, results, ack="secondary")

    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith("PROBE_SUMMARY ")
    payload = json.loads(last.removeprefix("PROBE_SUMMARY "))
    assert payload == {
        "paths": {"gemini": "PASS", "secondary": "NOT_CONFIGURED"},
        "acknowledged": ["secondary"],
        "exit_code": 0,
    }
    assert gh_output.read_text(encoding="utf-8") == (
        "states=gemini=PASS, secondary=NOT CONFIGURED (acknowledged)\n"
    )


def test_annotation_message_escapes_newlines() -> None:
    assert probe_failover._escape_annotation("a\nb%c\r") == "a%0Ab%25c%0D"


def test_end_to_end_gg_target_state_with_mocked_network(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """GEMINI_API_KEY set, secondary deliberately unset+acknowledged: real run_probe, no network."""
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv(ACK, "secondary")
    monkeypatch.setattr(sys, "argv", ["probe_failover.py"])
    with patch("app.core.config.get_settings", lambda: _settings(GEMINI_API_KEY="fake-key")):
        with patch(
            "app.core.llm._call_gemini",
            new_callable=AsyncMock,
            return_value=(_GOOD_EXTRACTION, 10, 5),
        ) as gemini_call:
            exit_code = probe_failover.main()

    out = capsys.readouterr().out
    assert exit_code == 0
    assert gemini_call.await_count == 1
    assert "[PASS] gemini" in out
    assert "[NOT CONFIGURED] secondary" in out
    assert "| secondary | NOT CONFIGURED (acknowledged) |" in summary.read_text(encoding="utf-8")
