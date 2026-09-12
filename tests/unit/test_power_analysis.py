"""Unit tests for eval/power_analysis.py -- MDE for a 2-proportion comparison."""

from __future__ import annotations

import pytest
from eval.power_analysis import minimum_detectable_effect, z_for_alpha, z_for_power


class TestZCriticalValues:
    def test_z_alpha_05_two_sided_matches_published_1_96(self):
        # Universally published critical value (any statistics z-table).
        assert z_for_alpha(0.05, two_sided=True) == pytest.approx(1.95996, abs=1e-4)

    def test_z_power_80_matches_published_0_84(self):
        # Universally published critical value for 80% power.
        assert z_for_power(0.80) == pytest.approx(0.84162, abs=1e-4)

    def test_z_alpha_invalid_range_raises(self):
        with pytest.raises(ValueError, match="alpha must be"):
            z_for_alpha(1.5)

    def test_z_power_invalid_range_raises(self):
        with pytest.raises(ValueError, match="power must be"):
            z_for_power(0.0)


class TestMinimumDetectableEffect:
    def test_matches_hand_computation_at_p_half_n_100(self):
        # delta = (1.95996 + 0.84162) * sqrt(2*0.5*0.5/100) = 2.80158 * sqrt(0.005)
        expected = (1.95996 + 0.84162) * (0.005**0.5)
        assert minimum_detectable_effect(100, p=0.5) == pytest.approx(expected, abs=1e-6)

    def test_larger_n_gives_smaller_mde(self):
        small_n_mde = minimum_detectable_effect(50, p=0.5)
        large_n_mde = minimum_detectable_effect(500, p=0.5)
        assert large_n_mde < small_n_mde

    def test_mde_scales_as_inverse_sqrt_n(self):
        # Quadrupling n should roughly halve the MDE (1/sqrt(4) = 0.5).
        mde_n = minimum_detectable_effect(100, p=0.5)
        mde_4n = minimum_detectable_effect(400, p=0.5)
        assert mde_4n == pytest.approx(mde_n / 2, rel=1e-6)

    def test_p_near_extremes_gives_smaller_mde_than_p_half(self):
        # Variance p(1-p) is maximized at p=0.5 -- MDE should be smaller away from it.
        mde_half = minimum_detectable_effect(100, p=0.5)
        mde_extreme = minimum_detectable_effect(100, p=0.85)
        assert mde_extreme < mde_half

    def test_zero_n_raises(self):
        with pytest.raises(ValueError, match="n must be positive"):
            minimum_detectable_effect(0)

    def test_negative_n_raises(self):
        with pytest.raises(ValueError, match="n must be positive"):
            minimum_detectable_effect(-10)

    def test_p_out_of_range_raises(self):
        with pytest.raises(ValueError, match="p must be in"):
            minimum_detectable_effect(100, p=1.0)
