"""Unit tests for the Wilson score confidence interval helper."""

from __future__ import annotations

import pytest
from eval.wilson import wilson_ci


class TestWilsonCi:
    def test_zero_n_returns_zero_interval(self):
        assert wilson_ci(0.9, 0) == (0.0, 0.0)

    def test_negative_n_returns_zero_interval(self):
        assert wilson_ci(0.9, -5) == (0.0, 0.0)

    def test_perfect_score_large_n_interval_narrows(self):
        # p=1.0 with more trials should give a tighter (higher) lower bound.
        lower_small_n, upper_small_n = wilson_ci(1.0, 5)
        lower_large_n, upper_large_n = wilson_ci(1.0, 500)
        assert lower_large_n > lower_small_n
        assert upper_small_n == pytest.approx(1.0, abs=1e-6)
        assert upper_large_n == pytest.approx(1.0, abs=1e-6)

    def test_perfect_score_never_exceeds_one(self):
        lower, upper = wilson_ci(1.0, 40)
        assert upper <= 1.0
        assert 0.0 < lower < 1.0

    def test_zero_score_never_below_zero(self):
        lower, upper = wilson_ci(0.0, 40)
        assert lower == 0.0
        assert 0.0 < upper < 1.0

    def test_symmetric_around_half_for_p_half(self):
        lower, upper = wilson_ci(0.5, 100)
        midpoint = (lower + upper) / 2
        assert midpoint == pytest.approx(0.5, abs=0.01)

    def test_known_value_p1_n40(self):
        # Cross-checked against the standard Wilson formula by hand:
        # z=1.96, p=1.0, n=40 -> lower ~= 0.9124, upper clamped to 1.0.
        lower, upper = wilson_ci(1.0, 40)
        assert lower == pytest.approx(0.9124, abs=1e-3)
        assert upper == pytest.approx(1.0, abs=1e-6)

    def test_out_of_range_p_is_clamped(self):
        # Defensive: a caller passing an out-of-range mean score shouldn't crash sqrt().
        lower, upper = wilson_ci(1.2, 10)
        assert 0.0 <= lower <= upper <= 1.0
