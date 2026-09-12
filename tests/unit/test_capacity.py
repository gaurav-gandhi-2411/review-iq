"""Unit tests for app/core/capacity.py -- Session 13 P3b's honest job-ETA estimate."""

from __future__ import annotations

import json
from pathlib import Path

from app.core.capacity import REAL_EXTRACTIONS_PER_MINUTE_CEILING, estimate_seconds_remaining


def test_zero_rows_remaining_is_zero_seconds() -> None:
    assert estimate_seconds_remaining(0) == 0.0


def test_negative_rows_remaining_is_zero_seconds() -> None:
    # Defensive: processed+failed should never exceed total, but never return a negative ETA
    # if it somehow does.
    assert estimate_seconds_remaining(-5) == 0.0


def test_positive_rows_remaining_scales_linearly() -> None:
    one = estimate_seconds_remaining(1)
    ten = estimate_seconds_remaining(10)
    assert ten == one * 10


def test_100_rows_takes_roughly_18_minutes_at_the_measured_ceiling() -> None:
    # Sanity-check against the known real-world figure this constant produces: ~5.6
    # extractions/minute means 100 rows take ~17.8 minutes (1068 seconds), not the "3.5/minute"
    # naive-shared-pool estimate (which would give ~28.6 minutes) -- pins the actual measured
    # value against silent drift toward a plausible-but-wrong round number.
    seconds = estimate_seconds_remaining(100)
    assert 1060 < seconds < 1075


class TestCeilingDoesNotSilentlyDriftFromTheMeasurement:
    """Session 13 P3b: app/core/capacity.py hardcodes a constant derived from
    eval/capacity_model.py's output (eval/ is not shipped in the production Docker image, so
    it cannot be read live at runtime -- see capacity.py's module docstring). If the
    underlying measurement is ever regenerated with different data, this constant must be
    updated to match, and this test is the tripwire that catches a missed update in CI.
    """

    def test_hardcoded_constant_matches_a_fresh_run_of_capacity_model(self) -> None:
        root = Path(__file__).resolve().parent.parent.parent
        capacity_model_path = root / "eval" / "results" / "capacity_model.json"
        if not capacity_model_path.exists():
            return  # eval/ artifacts aren't guaranteed present in every test environment
        data = json.loads(capacity_model_path.read_text(encoding="utf-8"))
        fresh = data["real_extractions_per_minute_ceiling"]
        assert abs(fresh - REAL_EXTRACTIONS_PER_MINUTE_CEILING) / fresh < 0.01, (
            f"eval/results/capacity_model.json reports {fresh}, but app/core/capacity.py "
            f"hardcodes {REAL_EXTRACTIONS_PER_MINUTE_CEILING} -- re-run "
            f"eval/capacity_model.py and update the constant to match."
        )
