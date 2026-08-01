"""Unit tests for eval/power_analysis.py -- MDE for a 2-proportion comparison."""

from __future__ import annotations

import pytest
from eval.power_analysis import (
    minimum_detectable_effect,
    paired_mde,
    required_n_for_paired_mde,
    z_for_alpha,
    z_for_power,
)


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


class TestPairedMDE:
    """Wave 2 P0: redaction accuracy-delta gate MDE correction. Real numbers, not
    illustrative -- these match the exact figures recorded in ADR 0005's amendment,
    extracted from the paired diffs behind the already-committed n=49 measurement
    (sd=4.0719pp), so a future accidental change to this formula would be caught here
    against a real, previously-verified case, not just a synthetic one."""

    _SD = 0.040719  # exact sample std dev of the 49 paired diffs, as a fraction

    def test_precision_framing_mde_at_n49_matches_redaction_gate_finding(self):
        # power=None -> pure 95% CI half-width, no power/beta term.
        mde = paired_mde(49, self._SD, power=None)
        assert mde * 100 == pytest.approx(1.1401, abs=1e-3)

    def test_precision_framing_required_n_for_1pp_matches_redaction_gate_finding(self):
        n = required_n_for_paired_mde(0.01, self._SD, power=None)
        assert n == 64

    def test_power80_framing_mde_at_n49_matches_redaction_gate_finding(self):
        mde = paired_mde(49, self._SD, power=0.80)
        assert mde * 100 == pytest.approx(1.6297, abs=1e-3)

    def test_power80_framing_required_n_for_1pp_matches_redaction_gate_finding(self):
        n = required_n_for_paired_mde(0.01, self._SD, power=0.80)
        assert n == 131

    def test_power80_is_more_conservative_than_precision_only(self):
        # Adding a power/beta term always widens the MDE (more evidence required).
        assert paired_mde(49, self._SD, power=0.80) > paired_mde(49, self._SD, power=None)

    def test_larger_n_gives_smaller_paired_mde(self):
        assert paired_mde(200, self._SD) < paired_mde(49, self._SD)

    def test_required_n_round_trips_to_target_mde_or_better(self):
        target = 0.015
        n = required_n_for_paired_mde(target, self._SD, power=0.80)
        assert paired_mde(n, self._SD, power=0.80) <= target
        # One fewer observation should fail to meet the target (n is the minimum).
        assert paired_mde(n - 1, self._SD, power=0.80) > target

    def test_zero_n_raises(self):
        with pytest.raises(ValueError, match="n must be positive"):
            paired_mde(0, self._SD)

    def test_negative_sd_raises(self):
        with pytest.raises(ValueError, match="sd must be non-negative"):
            paired_mde(49, -0.01)

    def test_zero_target_mde_raises(self):
        with pytest.raises(ValueError, match="target_mde must be positive"):
            required_n_for_paired_mde(0.0, self._SD)
