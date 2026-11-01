"""Unit tests for the pure helpers of the S20 load harness (scripts/load/harness.py)."""

from __future__ import annotations

from scripts.load.harness import StepResult, find_knee, percentile


def _step(n: int, rps: float, p95: float, err: float = 0.0) -> StepResult:
    return StepResult("s", n, 100, 0, rps, p95 / 2, p95, p95, err)


def test_percentile_nearest_rank() -> None:
    vals = [float(i) for i in range(1, 101)]
    assert percentile(vals, 0.50) == 50.0
    assert percentile(vals, 0.95) == 95.0
    assert percentile(vals, 0.99) == 99.0
    assert percentile([], 0.5) == 0.0
    assert percentile([7.0], 0.99) == 7.0


def test_find_knee_saturation_and_p95_doubling() -> None:
    steps = [_step(1, 100, 10), _step(2, 190, 12), _step(4, 200, 25), _step(8, 198, 90)]
    knee = find_knee(steps)
    assert knee["saturation"] == 2  # 190 >= 0.9 * 200
    assert knee["p95_2x"] == 4
    assert knee["errors"] is None


def test_find_knee_errors_and_empty() -> None:
    assert find_knee([_step(1, 10, 5), _step(2, 10, 6, err=0.5)])["errors"] == 2
    assert find_knee([]) == {"saturation": None, "p95_2x": None, "errors": None}
