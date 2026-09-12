"""Wilson score confidence interval — shared by eval/runner.py and eval/authenticity/runner.py.

Standard reference: Wilson, E.B. (1927), "Probable Inference, the Law of Succession, and
Statistical Inference", JASA 22(158).
"""

from __future__ import annotations

import math

# 95% two-sided normal critical value (Phi^-1(0.975)).
DEFAULT_Z = 1.96


def wilson_ci(p: float, n: int, z: float = DEFAULT_Z) -> tuple[float, float]:
    """Return the (lower, upper) Wilson score interval for a proportion `p` over `n` trials.

    Formula (see module docstring for citation):
        center = (p + z^2 / (2n)) / (1 + z^2 / n)
        margin = (z / (1 + z^2 / n)) * sqrt(p(1-p)/n + z^2 / (4n^2))
        lower, upper = center - margin, center + margin

    LIMITATION -- read before trusting this number: Wilson's interval assumes `n`
    independent Bernoulli trials, each strictly 0 or 1. Two different uses in this repo
    depart from that assumption in different ways:

      1. eval/runner.py's per-fixture "score" is a MEAN of several field-level scores
         (each in [0, 1]), not a single binary outcome. Treating that mean as a proportion
         and the fixture count as `n` gives a directionally-correct, order-of-magnitude
         uncertainty band -- useful for judging whether small eval-set noise could
         plausibly flip a pass/fail decision -- but it is NOT a statistically exact
         interval for a mean-of-continuous-scores estimator (that would require a
         t-interval or a bootstrap over fixture-level means).
      2. F1 is a harmonic mean of precision and recall, not itself a single binomial
         proportion, so no single "n" is exactly correct for it. Callers must pick and
         document an n (see eval/authenticity/runner.py for the convention used there).

    This function does not know which case it's being called for -- it just computes the
    interval for whatever (p, n) it's given. Don't oversell the precision of the result.

    Args:
        p: Observed proportion (or proportion-like mean score), clamped to [0, 1].
        n: Number of trials (or fixtures) the proportion was computed over.
        z: Critical value for the desired confidence level (default: 95%, two-sided).

    Returns:
        (lower, upper) bounds, each clamped to [0.0, 1.0]. Returns (0.0, 0.0) if n <= 0.
    """
    if n <= 0:
        return (0.0, 0.0)

    p_clamped = min(max(p, 0.0), 1.0)
    z_sq = z * z
    denom = 1.0 + z_sq / n
    center = (p_clamped + z_sq / (2 * n)) / denom
    margin = (z / denom) * math.sqrt((p_clamped * (1 - p_clamped) / n) + (z_sq / (4 * n * n)))

    lower = max(0.0, center - margin)
    upper = min(1.0, center + margin)
    return (lower, upper)
