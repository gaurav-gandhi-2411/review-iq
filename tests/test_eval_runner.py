"""Unit tests for eval/runner.py — routed-mode additions."""

from __future__ import annotations

# eval/ is not a package; import via importlib so pytest doesn't need an __init__.py there.
import importlib.util
import io
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_RUNNER_PATH = Path(__file__).parent.parent / "eval" / "runner.py"
_spec = importlib.util.spec_from_file_location("eval.runner", _RUNNER_PATH)
assert _spec is not None and _spec.loader is not None
_runner = importlib.util.module_from_spec(_spec)
# Register before exec so @dataclass can resolve cls.__module__ via sys.modules.
sys.modules["eval.runner"] = _runner
_spec.loader.exec_module(_runner)  # type: ignore[union-attr]

FixtureResult = _runner.FixtureResult
print_token_summary = _runner.print_token_summary
run_all_routed = _runner.run_all_routed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(
    fixture_id: str = "test_001",
    tier: str = "small",
    escalated: bool = False,
    tokens_in: int = 100,
    tokens_out: int = 50,
    overall_score: float = 1.0,
    error: str | None = None,
) -> FixtureResult:
    r = FixtureResult(fixture_id=fixture_id)
    r.tier = tier
    r.escalated = escalated
    r.tokens_in = tokens_in
    r.tokens_out = tokens_out
    r.overall_score = overall_score
    r.error = error
    return r


def _capture_stdout(fn: object, *args: object) -> str:
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        fn(*args)  # type: ignore[operator]
    finally:
        sys.stdout = old
    return buf.getvalue()


# ---------------------------------------------------------------------------
# FixtureResult dataclass — new fields have correct defaults
# ---------------------------------------------------------------------------


def test_fixture_result_new_field_defaults() -> None:
    """New routed fields default to empty/zero/False so existing code is unaffected."""
    r = FixtureResult(fixture_id="x")
    assert r.tier == ""
    assert r.escalated is False
    assert r.tokens_in == 0
    assert r.tokens_out == 0


# ---------------------------------------------------------------------------
# print_token_summary — no routed results
# ---------------------------------------------------------------------------


def test_print_token_summary_no_routed_results() -> None:
    """print_token_summary is a no-op when no result has a tier set."""
    results = [FixtureResult(fixture_id="x")]  # tier="" by default
    out = _capture_stdout(print_token_summary, results)
    assert out == ""


# ---------------------------------------------------------------------------
# print_token_summary — all small, no large
# ---------------------------------------------------------------------------


def test_print_token_summary_all_small() -> None:
    """When every fixture routes to small (no large calls), the no-baseline message is shown."""
    results = [
        _make_result("a", tier="small", tokens_in=100, tokens_out=40),
        _make_result("b", tier="small", tokens_in=120, tokens_out=50),
    ]
    out = _capture_stdout(print_token_summary, results)
    assert "Total fixtures:  2" in out
    assert "Small tier:      2" in out
    assert "Large tier:      0" in out
    # The else branch fires when large list is empty (no large-tier calls to compute baseline from).
    assert "no token reduction to report" in out
    # No percentage savings line should appear.
    assert "Token reduction:" not in out


# ---------------------------------------------------------------------------
# print_token_summary — mixed small + large, one escalated
# ---------------------------------------------------------------------------


def test_print_token_summary_mixed_tiers() -> None:
    """Token reduction is computed correctly against all-large baseline."""
    results = [
        _make_result("a", tier="small", tokens_in=80, tokens_out=30),
        _make_result("b", tier="large", tokens_in=200, tokens_out=80),
        _make_result("c", tier="large", escalated=True, tokens_in=220, tokens_out=90),
    ]
    out = _capture_stdout(print_token_summary, results)
    assert "Total fixtures:  3" in out
    assert "Small tier:      1" in out
    assert "Large tier:      2" in out
    assert "escalated: 1" in out
    # total_in = 80 + 200 + 220 = 500
    # avg_large_in = (200 + 220) / 2 = 210; direct_est = 210 * 3 = 630
    # pct_saved = (630 - 500) / 630 * 100 ≈ 20.6%
    assert "630" in out
    assert "20.6%" in out


# ---------------------------------------------------------------------------
# print_token_summary — all large (edge case: small_in = 0)
# ---------------------------------------------------------------------------


def test_print_token_summary_all_large() -> None:
    """All fixtures on large tier: 0% token reduction reported."""
    results = [
        _make_result("a", tier="large", tokens_in=200, tokens_out=80),
        _make_result("b", tier="large", tokens_in=200, tokens_out=80),
    ]
    out = _capture_stdout(print_token_summary, results)
    assert "Small tier:      0" in out
    assert "Large tier:      2" in out
    # direct_est == total_in, pct_saved == 0.0
    assert "0.0%" in out


# ---------------------------------------------------------------------------
# run_all_routed — happy path with mocked run_single_routed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_all_routed_calls_run_single_routed(tmp_path: Path) -> None:
    """run_all_routed iterates fixture files and delegates to run_single_routed."""
    # Write a minimal fixture JSON.
    (tmp_path / "tst_001.json").write_text(
        '{"id":"tst_001","review_text":"x","ground_truth":{"language":"en"},"scoring_notes":{}}',
        encoding="utf-8",
    )

    fake_result = _make_result("tst_001", tier="small", tokens_in=80, overall_score=1.0)

    with patch.object(_runner, "run_single_routed", AsyncMock(return_value=fake_result)):
        results = await run_all_routed(fixtures_dir=tmp_path)

    assert len(results) == 1
    assert results[0].fixture_id == "tst_001"
    assert results[0].tier == "small"
