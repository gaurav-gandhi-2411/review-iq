"""Unit tests for scripts/measure_redaction_accuracy_delta.py's pure-function pieces.

Does NOT exercise `_score_fixture`/`main` -- those require the full extraction pipeline
and cassette store (covered by manually running the script, see the Wave 1 Section E
report). This file covers the bootstrap-CI math in isolation, which is what makes the
delta gate's number trustworthy.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.measure_redaction_accuracy_delta import _bootstrap_ci  # noqa: E402


class TestBootstrapCi:
    def test_empty_diffs_returns_zero(self) -> None:
        assert _bootstrap_ci([]) == (0.0, 0.0)

    def test_zero_diffs_ci_is_zero_width(self) -> None:
        """When every paired diff is exactly 0.0, the CI must collapse to (0.0, 0.0) --
        this is the exact case the Wave 1 Section E delta measurement hit (30/49
        unblocked fixtures had a byte-identical prompt in both arms)."""
        lo, hi = _bootstrap_ci([0.0] * 30)
        assert lo == 0.0
        assert hi == 0.0

    def test_ci_brackets_the_true_mean_for_constant_diff(self) -> None:
        diffs = [0.1] * 20
        lo, hi = _bootstrap_ci(diffs)
        assert lo == pytest.approx(0.1)
        assert hi == pytest.approx(0.1)

    def test_ci_widens_with_more_variance(self) -> None:
        low_variance = [0.05, 0.05, 0.06, 0.04, 0.05] * 4
        high_variance = [-0.5, 0.5, -0.4, 0.6, 0.0] * 4
        lo1, hi1 = _bootstrap_ci(low_variance)
        lo2, hi2 = _bootstrap_ci(high_variance)
        assert (hi2 - lo2) > (hi1 - lo1)

    def test_deterministic_given_fixed_seed(self) -> None:
        """seed=42 hardcoded (house convention) -- two runs on the same input must
        produce byte-identical bounds, not just similar ones."""
        diffs = [0.1, -0.2, 0.3, 0.0, -0.1, 0.05]
        result1 = _bootstrap_ci(diffs, resamples=2000, seed=42)
        result2 = _bootstrap_ci(diffs, resamples=2000, seed=42)
        assert result1 == result2
