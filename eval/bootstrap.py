"""Paired percentile-bootstrap confidence intervals over eval fixtures.

This is the interval eval/runner.py reports next to its headline per-language and
overall scores -- not eval/wilson.py's Wilson score interval. Reason: a Wilson interval
assumes `n` independent Bernoulli trials, each strictly 0 or 1. eval/runner.py's
per-fixture "score" is a MEAN of several continuous field-level scores in [0, 1] (see
score_fixture()); observing e.g. hi's 0.8071 over n=7 fixtures is not "5.65 successes out
of 7 trials" -- there is no trial here that resolved to a strict 0 or 1, so a binomial
interval does not describe this estimator, no matter how wide the resulting band already
looks (see eval/wilson.py's own documented limitation, which flags exactly this without
fixing it).

The bootstrap respects the actual estimator instead: resample the list of per-fixture
scores WITH REPLACEMENT (each resampled unit is a whole fixture, so all of that fixture's
field-level scores move together -- this captures within-fixture correlation between
field errors for free, which an independent-per-field model would miss), recompute the
mean for each resample, and take empirical percentiles of the resulting distribution.

Standard reference: Efron & Tibshirani, "An Introduction to the Bootstrap" (1993),
ch. 13 (the percentile method).

LIMITATION -- read before trusting this at very small n, and do not repeat the mistake
this docstring exists to correct: at n=7 (hi) or n=15 (hi-en), this is NOT a precise
population-level estimate. A resample can only ever contain values drawn FROM the
original observed scores, so no resample mean can fall outside the convex hull of the
input -- the whole distribution is built from 7 (or 15) numbers. Percentile-bootstrap
coverage is known to degrade badly below roughly n=30 (Efron & Tibshirani, ch. 13);
correct estimator or not, an interval computed this way at n=7 tells you "these 7
fixtures are internally consistent with each other," not "the true accuracy is known to
within this band." A narrow interval at this n is NOT reassuring on its own -- if the
underlying sample is synthetic and/or the prompt was tuned against it (see eval/README.md's
D3 contamination discussion), a narrow bootstrap CI is exactly what contamination
produces, and is a warning sign, not a comfort. This module was itself first shipped
with a required-n-for-corpus-growth claim derived from applying this method naively to
review-iq's contaminated `hi` set -- that claim was wrong and has been removed; see
`required_n_for_half_width`'s docstring for the specifics. Do not compute or publish a
required-n claim from this module's output against a small, synthetic, or prompt-tuned
sample. See eval/power_analysis.py for the (also currently blocked) proper path to that
number.
"""

from __future__ import annotations

import random
from statistics import mean

DEFAULT_N_RESAMPLES = 10_000
DEFAULT_SEED = 42  # this repo's standing convention for anything stochastic


def bootstrap_ci(
    scores: list[float],
    n_resamples: int = DEFAULT_N_RESAMPLES,
    seed: int = DEFAULT_SEED,
    lower_pct: float = 2.5,
    upper_pct: float = 97.5,
) -> tuple[float, float]:
    """Return the (lower, upper) percentile-bootstrap CI for the mean of `scores`.

    Args:
        scores: per-fixture (or per-item) scores to resample, each in [0, 1]. Order does
            not matter.
        n_resamples: number of bootstrap draws (default 10,000). A pinned seed makes the
            result exactly reproducible run-to-run and machine-to-machine.
        seed: RNG seed (default 42).
        lower_pct / upper_pct: percentile bounds (default 2.5/97.5 -> a 95% interval).

    Returns:
        (lower, upper), each clamped to [0.0, 1.0]. Returns (0.0, 0.0) if `scores` is
        empty.
    """
    if not scores:
        return (0.0, 0.0)

    rng = random.Random(seed)
    n = len(scores)
    resample_means = [mean(scores[rng.randrange(n)] for _ in range(n)) for _ in range(n_resamples)]
    resample_means.sort()

    lower_idx = int((lower_pct / 100) * n_resamples)
    upper_idx = min(int((upper_pct / 100) * n_resamples), n_resamples - 1)
    lower = max(0.0, resample_means[lower_idx])
    upper = min(1.0, resample_means[upper_idx])
    return (lower, upper)


def required_n_for_half_width(
    scores: list[float],
    target_half_width: float,
    z: float = 1.96,
) -> float:
    """Estimate the fixture count needed to shrink a bootstrap CI to `target_half_width`.

    Uses the standard large-sample scaling law for the standard error of a mean,
    SE(n) ~= sd / sqrt(n), extrapolated FROM this sample's own observed standard
    deviation (not an assumed Bernoulli p(1-p) variance, which would systematically
    understate the true variance of a continuous per-fixture score -- see this module's
    docstring). Required n for a target half-width `e` at critical value `z`:

        n_required = (z * sd / e) ** 2

    This is an approximation (it assumes the new fixtures added would have the same
    score variance as the current sample, and that a normal approximation holds at the
    target n) -- treat it as a planning target for corpus growth, not a guarantee.

    DO NOT call this against a sample that is (a) synthetic/generator-produced rather
    than drawn from real held-out data, or (b) a set the prompt under test was itself
    tuned against (see eval/README.md's D3 contamination discussion). A low observed
    variance in either case is what contamination produces, not evidence of a small
    required n -- a narrow estimate from a contaminated or synthetic sample is actively
    misleading, not just imprecise. This was gotten wrong once already: an earlier draft
    of Session 2's work applied this function to review-iq's 7 synthetic, prompt-tuned
    `hi` fixtures and reported "~9" as if it were a real planning number. It is not
    valid until re-derived against a genuine held-out pilot of real (non-synthetic)
    data that the prompt was never iterated against.

    Args:
        scores: the current sample of per-fixture scores to estimate variance from.
        target_half_width: desired half-width of the 95% interval, e.g. 0.05 for +/-5pp.
        z: critical value for the desired confidence level (default 1.96, 95% two-sided).

    Returns:
        Estimated required n (float, round up in the caller). Returns float("inf") if
        the sample has fewer than 2 points (sample sd undefined) or zero variance.
    """
    n = len(scores)
    if n < 2:
        return float("inf")
    m = mean(scores)
    sample_var = sum((s - m) ** 2 for s in scores) / (n - 1)
    if sample_var <= 0:
        return float("inf")
    sd = sample_var**0.5
    return (z * sd / target_half_width) ** 2
