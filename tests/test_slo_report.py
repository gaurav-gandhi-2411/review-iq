"""Unit tests for scripts/slo_report.py's pure computation logic.

Network calls (Cloud Monitoring / Cloud Logging / ADC token refresh) are mocked --
this suite never makes a live GCP call, matching the eval CI convention of zero
live calls in the test suite itself (scripts/slo_report.py's live behaviour was
verified manually and is documented in the Section F report, not re-verified here).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import slo_report  # noqa: E402 -- must follow the sys.path insert above

# ---------------------------------------------------------------------------
# _percentile
# ---------------------------------------------------------------------------


def test_percentile_median() -> None:
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert slo_report._percentile(values, 0.5) == 30.0


def test_percentile_p95_of_larger_set() -> None:
    values = [float(i) for i in range(1, 101)]  # 1..100
    assert slo_report._percentile(values, 0.95) == 96.0


def test_percentile_empty_list_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        slo_report._percentile([], 0.5)


def test_percentile_single_value() -> None:
    assert slo_report._percentile([42.0], 0.99) == 42.0


# ---------------------------------------------------------------------------
# SloReport.latency_is_slo_grade
# ---------------------------------------------------------------------------


def test_latency_is_slo_grade_below_floor() -> None:
    report = slo_report.SloReport(
        project="p",
        service="s",
        window_days=14,
        total_requests=100,
        non_5xx_requests=99,
        availability_pct=99.0,
        extraction_count=2,
        latency_p50_ms=100.0,
        latency_p95_ms=200.0,
        latency_p99_ms=300.0,
        min_n=100,
    )
    assert report.latency_is_slo_grade is False


def test_latency_is_slo_grade_at_floor() -> None:
    report = slo_report.SloReport(
        project="p",
        service="s",
        window_days=14,
        total_requests=1000,
        non_5xx_requests=995,
        availability_pct=99.5,
        extraction_count=100,
        latency_p50_ms=800.0,
        latency_p95_ms=1500.0,
        latency_p99_ms=2000.0,
        min_n=100,
    )
    assert report.latency_is_slo_grade is True


# ---------------------------------------------------------------------------
# build_report — network calls mocked
# ---------------------------------------------------------------------------


def test_build_report_computes_availability_and_percentiles() -> None:
    with (
        patch.object(slo_report, "fetch_availability", return_value=(1000, 990)),
        patch.object(
            slo_report,
            "fetch_extraction_latencies",
            return_value=[float(i) for i in range(1, 101)],
        ),
    ):
        report = slo_report.build_report(
            "review-iq-prod", "review-iq", 14, min_n=100, token="fake-token"
        )

    assert report.total_requests == 1000
    assert report.non_5xx_requests == 990
    assert report.availability_pct == pytest.approx(99.0)
    assert report.extraction_count == 100
    assert report.latency_p95_ms == 96.0
    assert report.latency_is_slo_grade is True


def test_build_report_zero_traffic_does_not_divide_by_zero() -> None:
    with (
        patch.object(slo_report, "fetch_availability", return_value=(0, 0)),
        patch.object(slo_report, "fetch_extraction_latencies", return_value=[]),
    ):
        report = slo_report.build_report(
            "review-iq-prod", "review-iq", 14, min_n=100, token="fake-token"
        )

    assert report.availability_pct == 0.0
    assert report.latency_p50_ms is None
    assert report.latency_is_slo_grade is False


# ---------------------------------------------------------------------------
# print_report — insufficient-data messaging
# ---------------------------------------------------------------------------


def test_print_report_flags_insufficient_data(capsys: pytest.CaptureFixture[str]) -> None:
    report = slo_report.SloReport(
        project="review-iq-prod",
        service="review-iq",
        window_days=14,
        total_requests=7115,
        non_5xx_requests=7113,
        availability_pct=99.972,
        extraction_count=2,
        latency_p50_ms=762.0,
        latency_p95_ms=2922.0,
        latency_p99_ms=2922.0,
        min_n=100,
    )
    slo_report.print_report(report)
    out = capsys.readouterr().out
    assert "INSUFFICIENT DATA" in out
    assert "n=2" in out


def test_print_report_prints_percentiles_when_slo_grade(
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = slo_report.SloReport(
        project="review-iq-prod",
        service="review-iq",
        window_days=14,
        total_requests=100_000,
        non_5xx_requests=99_500,
        availability_pct=99.5,
        extraction_count=150,
        latency_p50_ms=800.0,
        latency_p95_ms=1500.0,
        latency_p99_ms=2000.0,
        min_n=100,
    )
    slo_report.print_report(report)
    out = capsys.readouterr().out
    assert "INSUFFICIENT DATA" not in out
    assert "p95=1500ms" in out
