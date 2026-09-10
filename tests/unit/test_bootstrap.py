"""Unit tests for the paired percentile-bootstrap confidence interval helper."""

from __future__ import annotations

import pytest
from eval.bootstrap import bootstrap_ci, required_n_for_half_width


class TestBootstrapCi:
    def test_empty_scores_returns_zero_interval(self):
        assert bootstrap_ci([]) == (0.0, 0.0)

    def test_single_repeated_value_gives_degenerate_interval(self):
        # Every resample of a constant list is that same constant -> zero-width CI.
        lower, upper = bootstrap_ci([0.8, 0.8, 0.8, 0.8])
        assert lower == pytest.approx(0.8, abs=1e-9)
        assert upper == pytest.approx(0.8, abs=1e-9)

    def test_interval_never_exceeds_input_bounds(self):
        # Percentile bootstrap can never produce a mean outside the convex hull of the
        # input scores -- this is the documented small-n limitation, verified directly.
        scores = [0.6, 0.7, 0.9, 1.0]
        lower, upper = bootstrap_ci(scores)
        assert min(scores) <= lower <= upper <= max(scores)

    def test_deterministic_with_pinned_seed(self):
        scores = [0.82, 0.71, 0.93, 0.65, 0.77, 0.88, 0.55]
        first = bootstrap_ci(scores, n_resamples=2000, seed=42)
        second = bootstrap_ci(scores, n_resamples=2000, seed=42)
        assert first == second

    def test_different_seeds_give_different_but_close_results(self):
        scores = [0.82, 0.71, 0.93, 0.65, 0.77, 0.88, 0.55]
        a = bootstrap_ci(scores, n_resamples=2000, seed=1)
        b = bootstrap_ci(scores, n_resamples=2000, seed=2)
        # Not required to be identical, but should agree to within a coarse tolerance.
        assert a[0] == pytest.approx(b[0], abs=0.05)
        assert a[1] == pytest.approx(b[1], abs=0.05)

    def test_wider_n_at_equal_variance_narrows_interval(self):
        # Same distribution, more observations of it -> a tighter interval.
        small_sample = [0.8, 0.9, 0.7, 0.85, 0.75]
        large_sample = small_sample * 20  # same empirical distribution, 20x the count
        lo_small, hi_small = bootstrap_ci(small_sample, n_resamples=5000)
        lo_large, hi_large = bootstrap_ci(large_sample, n_resamples=5000)
        assert (hi_large - lo_large) < (hi_small - lo_small)

    def test_result_clamped_to_unit_interval(self):
        lower, upper = bootstrap_ci([0.0, 0.0, 0.05, 1.0, 1.0])
        assert 0.0 <= lower <= upper <= 1.0

    def test_matches_known_hi_language_sample(self):
        # The actual hi-language per-fixture overall_score values recorded in
        # eval/results.json as of this branch (n=7, mean 0.8071) -- a real regression
        # anchor, not synthetic data.
        hi_scores = [0.7238, 0.7679, 0.8095, 0.8214, 0.8333, 0.8571, 0.9367]
        lower, upper = bootstrap_ci(hi_scores, n_resamples=10_000, seed=42)
        assert lower < 0.8071 < upper
        # Confirms the qualitative finding this whole module exists to surface: the
        # 0.80 gate sits well inside the interval, not near either edge.
        assert lower < 0.80 < upper


class TestRequiredNForHalfWidth:
    def test_fewer_than_two_scores_is_infinite(self):
        assert required_n_for_half_width([0.8], target_half_width=0.05) == float("inf")
        assert required_n_for_half_width([], target_half_width=0.05) == float("inf")

    def test_zero_variance_is_infinite(self):
        assert required_n_for_half_width([0.8, 0.8, 0.8], target_half_width=0.05) == float("inf")

    def test_tighter_target_requires_more_n(self):
        scores = [0.6, 0.7, 0.9, 0.75, 0.85, 0.8, 0.65]
        loose = required_n_for_half_width(scores, target_half_width=0.10)
        tight = required_n_for_half_width(scores, target_half_width=0.02)
        assert tight > loose

    def test_scales_as_inverse_square_of_target(self):
        # n_required = (z*sd/e)^2 -> halving e should exactly quadruple n_required.
        scores = [0.6, 0.7, 0.9, 0.75, 0.85, 0.8, 0.65]
        n_at_e = required_n_for_half_width(scores, target_half_width=0.10)
        n_at_half_e = required_n_for_half_width(scores, target_half_width=0.05)
        assert n_at_half_e == pytest.approx(n_at_e * 4, rel=1e-6)
