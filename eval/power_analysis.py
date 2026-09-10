"""Minimum detectable effect (MDE) for a 2-proportion comparison -- standard formula.

Answers: "given an eval set of this size, how big a percentage-point regression could
we reliably detect?" Standard two-independent-proportions z-test, solved for the
minimum detectable absolute difference `delta` given a fixed sample size `n` per arm
(the usual framing for "is my eval set big enough"), rather than the more common
textbook direction (solve for required `n` given a target `delta`) -- same formula,
rearranged for the question this repo actually needs answered.

Formula (Cohen, "Statistical Power Analysis", 2nd ed.; also e.g. Fleiss, Levin & Paik,
"Statistical Methods for Rates and Proportions", 3rd ed., ch. 4): for two independent
samples of size `n` each, testing H0: p1 = p2 at two-sided significance `alpha` with
power `1 - beta`:

    n = (z_(alpha/2) + z_beta)^2 * [p1(1-p1) + p2(1-p2)] / (p1 - p2)^2

Solved for `delta = |p1 - p2|` given `n`, using `p1 ~= p2 ~= p` (the near-null
approximation standard for MDE reporting -- exact only when the true effect is small,
which is exactly the regime an MDE claim is about):

    delta_min = (z_(alpha/2) + z_beta) * sqrt(2 * p * (1 - p) / n)

`z_(alpha/2)` and `z_beta` are the standard normal quantiles -- 1.95996 for
alpha=0.05 (two-sided) and 0.84162 for power=0.80, both universally-published critical
values (any statistics textbook's z-table), computed here via
`statistics.NormalDist().inv_cdf(...)` rather than hardcoded, so the formula stays
correct for any alpha/power the caller passes.
"""

from __future__ import annotations

import math
from statistics import NormalDist

_NORMAL = NormalDist()


def z_for_alpha(alpha: float = 0.05, two_sided: bool = True) -> float:
    """Return the standard normal critical value for significance level `alpha`.

    z_(0.05, two-sided) = 1.95996 (i.e. ~1.96, the universally cited value).
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
    tail_prob = 1 - (alpha / 2 if two_sided else alpha)
    return _NORMAL.inv_cdf(tail_prob)


def z_for_power(power: float = 0.80) -> float:
    """Return the standard normal critical value for statistical power `power`.

    z_(power=0.80) = 0.84162 (the universally cited value for 80% power).
    """
    if not 0.0 < power < 1.0:
        raise ValueError(f"power must be in (0, 1), got {power}")
    return _NORMAL.inv_cdf(power)


def minimum_detectable_effect(
    n: int, p: float = 0.5, alpha: float = 0.05, power: float = 0.80
) -> float:
    """Minimum detectable absolute difference between two proportions at sample size `n`.

    Args:
        n: Sample size per arm (e.g. number of eval fixtures scored under each of two
            prompt/model versions).
        p: Baseline proportion the comparison is centered on (e.g. current eval pass
            rate). Defaults to 0.5, the maximum-variance / most conservative case --
            using the repo's actual current pass rate (see eval/results/latest.json)
            gives a tighter, more realistic number and should be preferred when known.
        alpha: Two-sided significance level (default 0.05).
        power: Desired statistical power, 1 - beta (default 0.80).

    Returns:
        The smallest |p1 - p2| this sample size could detect at the given alpha/power,
        as an absolute proportion (multiply by 100 for percentage points).

    Raises:
        ValueError: `n <= 0`, or `p` not in (0, 1).
    """
    if n <= 0:
        raise ValueError(f"n must be positive, got {n}")
    if not 0.0 < p < 1.0:
        raise ValueError(f"p must be in (0, 1), got {p}")
    z = z_for_alpha(alpha) + z_for_power(power)
    return z * math.sqrt(2 * p * (1 - p) / n)
