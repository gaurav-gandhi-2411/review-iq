"""Inter-rater agreement statistics — Krippendorff's alpha and Fleiss' kappa.

Built for Wave 1 Section B ("kill hand-labeling"): the multi-LLM consensus labeler
(`eval/consensus/labeler.py`) needs a real, textbook-verified measure of how much the
3-judge panel actually agrees, per field, not just a hand-wavy "3/3 unanimous" count.

Why two different statistics, not one:
  - Krippendorff's alpha is the primary measure here. It tolerates MISSING ratings
    (a judge that errored/timed out on one candidate doesn't force us to drop that
    candidate from every other judge's agreement score) and supports nominal, ordinal,
    and interval distance metrics in one formula. This matches our real data: judges
    occasionally fail to return a parseable response for a single field.
  - Fleiss' kappa is a secondary cross-check, computed only on the fully-covered
    subset (every item rated by all 3 judges) for categorical fields. It has no notion
    of "near-miss" for ordinal fields (urgency low/medium/high) -- a kappa-only view
    would incorrectly treat "low vs medium" the same as "low vs high" -- which is why
    alpha (with the ordinal distance metric) is primary for urgency/stars and kappa is
    reported alongside as the more commonly-cited number, not as the deciding one.

Level-of-measurement choice per field (see eval/consensus/labeler.py FIELD_SPECS):
  - nominal: sentiment, buy_again, language -- no natural order between categories.
  - ordinal: urgency (low < medium < high), stars/stars_inferred (1..5) -- categories
    have a real order and a "near miss" should count as less disagreement than a
    "polar opposite" miss.
  - Free-text / open-list fields (product, pros, cons, feature_requests, topics,
    competitor_mentions) are NOT run through alpha/kappa here -- they are already
    scored by fuzzy/set-overlap methods in eval/runner.py and don't reduce to a small
    fixed category set the way sentiment/urgency/language/buy_again/stars do.

Formulas implemented directly from Krippendorff's own general form (reproduced with
full worked arithmetic on Wikipedia's "Krippendorff's alpha" article, itself citing
Krippendorff, K. (2011) "Computing Krippendorff's Alpha-Reliability") and from
Fleiss, J.L. (1971) "Measuring nominal scale agreement among many raters",
Psychological Bulletin 76(5) (worked example reproduced on Wikipedia's "Fleiss's
kappa" article). See tests/unit/test_agreement.py -- both are unit-tested against
those exact published worked examples with known expected values (alpha_nominal
≈ 0.691, alpha_interval ≈ 0.811, Fleiss kappa ≈ 0.210), not just against this
module's own output.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Hashable, Sequence
from typing import Literal

LevelOfMeasurement = Literal["nominal", "ordinal", "interval"]


def _units_from_raters_matrix(
    reliability_data: Sequence[Sequence[Hashable | None]],
) -> list[Counter[Hashable]]:
    """Transpose a raters-by-units matrix into per-unit value counts, dropping unpairable units.

    `reliability_data[r][u]` is rater `r`'s value for unit `u`, or `None` if that rater
    didn't rate that unit. A unit with fewer than 2 non-None ratings contributes no
    pairable values and is dropped, per Krippendorff's definition.
    """
    n_raters = len(reliability_data)
    n_units = len(reliability_data[0]) if n_raters else 0
    units: list[Counter[Hashable]] = []
    for u in range(n_units):
        counts: Counter[Hashable] = Counter()
        for r in range(n_raters):
            v = reliability_data[r][u]
            if v is not None:
                counts[v] += 1
        if sum(counts.values()) >= 2:
            units.append(counts)
    return units


def _nominal_delta(v: Hashable, vp: Hashable, marginals: dict[Hashable, float]) -> float:
    del marginals  # unused for nominal -- signature kept uniform across delta functions
    return 0.0 if v == vp else 1.0


def _interval_delta(v: Hashable, vp: Hashable, marginals: dict[Hashable, float]) -> float:
    del marginals
    return (v - vp) ** 2  # type: ignore[operator]  # caller guarantees numeric values


def _ordinal_delta(
    v: Hashable, vp: Hashable, marginals: dict[Hashable, float], rank: dict[Hashable, int]
) -> float:
    """Krippendorff's ordinal distance: (sum of marginal freqs from rank(v) to rank(v') - avg)^2."""
    lo, hi = (v, vp) if rank[v] <= rank[vp] else (vp, v)
    lo_r, hi_r = rank[lo], rank[hi]
    total = sum(marginals[c] for c, r in rank.items() if lo_r <= r <= hi_r)
    return (total - (marginals[v] + marginals[vp]) / 2) ** 2


def krippendorff_alpha(
    reliability_data: Sequence[Sequence[Hashable | None]],
    level_of_measurement: LevelOfMeasurement = "nominal",
    categories: Sequence[Hashable] | None = None,
) -> float | None:
    """Compute Krippendorff's alpha for a raters-by-units reliability matrix.

    Args:
        reliability_data: `reliability_data[r][u]` = rater r's value for unit u, or
            `None` for a missing/unavailable rating. Any number of raters and units.
        level_of_measurement: "nominal" (no order), "ordinal" (ranked categories,
            requires `categories`), or "interval" (numeric, distance = squared diff).
        categories: Required for "ordinal" -- the categories in increasing rank order
            (e.g. `["low", "medium", "high"]`). Ignored otherwise.

    Returns:
        alpha in (-inf, 1.0], or `None` if there are fewer than 2 pairable units (alpha
        is undefined with no pairable data) or the expected disagreement is 0 with any
        observed disagreement (degenerate single-category-everywhere case).

    Raises:
        ValueError: `level_of_measurement` is "ordinal" without `categories`, or is
            not one of the three recognised values.
    """
    units = _units_from_raters_matrix(reliability_data)
    if not units:
        return None

    # Observed coincidence matrix o[v][v'] (Krippendorff's alpha, "Coincidence matrices").
    o: dict[Hashable, dict[Hashable, float]] = {}
    for counts in units:
        m_u = sum(counts.values())
        for v, cv in counts.items():
            o.setdefault(v, {}).setdefault(v, 0.0)
            o[v][v] += cv * (cv - 1) / (m_u - 1)
            for vp, cvp in counts.items():
                if vp == v:
                    continue
                o.setdefault(v, {}).setdefault(vp, 0.0)
                o[v][vp] += cv * cvp / (m_u - 1)

    all_values: set[Hashable] = set()
    for counts in units:
        all_values.update(counts.keys())

    n_v = {v: sum(o.get(v, {}).get(vp, 0.0) for vp in all_values) for v in all_values}
    n = sum(n_v.values())
    if n < 2:
        return None

    if level_of_measurement == "nominal":

        def delta(v: Hashable, vp: Hashable) -> float:
            return _nominal_delta(v, vp, n_v)
    elif level_of_measurement == "interval":

        def delta(v: Hashable, vp: Hashable) -> float:
            return _interval_delta(v, vp, n_v)
    elif level_of_measurement == "ordinal":
        if categories is None:
            raise ValueError(
                "level_of_measurement='ordinal' requires explicit `categories` rank order "
                "(e.g. categories=['low', 'medium', 'high']) -- string/mixed categories have "
                "no natural sort order we can infer safely."
            )
        rank = {c: i for i, c in enumerate(categories)}

        def delta(v: Hashable, vp: Hashable) -> float:
            return _ordinal_delta(v, vp, n_v, rank)
    else:
        raise ValueError(
            f"unknown level_of_measurement: {level_of_measurement!r} "
            "(expected 'nominal', 'ordinal', or 'interval')"
        )

    d_o = 0.0
    d_e = 0.0
    for v in all_values:
        for vp in all_values:
            if v == vp:
                continue  # delta(v, v) == 0 by definition -- skip for speed, not correctness
            d = delta(v, vp)
            d_o += o.get(v, {}).get(vp, 0.0) * d
            d_e += n_v[v] * n_v[vp] * d

    d_o = d_o / n
    d_e = d_e / (n * (n - 1))

    if d_e == 0:
        # Every pairable rating fell in one category (or ties out to zero expected
        # disagreement) -- alpha is undefined by the standard formula's 0/0. If observed
        # disagreement is also 0 this is perfect trivial agreement; otherwise undefined.
        return 1.0 if d_o == 0 else None
    return 1 - d_o / d_e


def fleiss_kappa(table: Sequence[Sequence[int]]) -> float | None:
    """Compute Fleiss' kappa for a fixed-n-raters-per-item categorical rating table.

    Args:
        table: `table[i][j]` = number of raters who assigned item i to category j.
            Every row must sum to the same total rater count `n` (Fleiss' 1971
            assumption of a fixed number of raters per item -- unlike Krippendorff's
            alpha, this does not tolerate a varying number of raters per item; callers
            should pre-filter to items with full rater coverage).

    Returns:
        kappa in (-inf, 1.0], or `None` if `table` is empty, or `P_e == 1.0` with
        `P_bar != 1.0` (degenerate: every rating in one category yet imperfect
        agreement is impossible under that condition, so this is a defensive guard
        that should not trigger on real data).

    Raises:
        ValueError: rows do not all sum to the same rater count `n`.
    """
    if not table:
        return None
    n_items = len(table)
    n_raters = sum(table[0])
    n_categories = len(table[0])
    for row in table:
        if sum(row) != n_raters:
            raise ValueError(
                "fleiss_kappa requires every item to have the same total rater count "
                f"(first row sums to {n_raters}, found a row summing to {sum(row)}) -- "
                "pre-filter to fully-covered items before calling this."
            )

    p_j = [sum(row[j] for row in table) / (n_items * n_raters) for j in range(n_categories)]
    p_i = [(sum(x * x for x in row) - n_raters) / (n_raters * (n_raters - 1)) for row in table]
    p_bar = sum(p_i) / n_items
    p_e = sum(x * x for x in p_j)
    if p_e == 1.0:
        return 1.0 if p_bar == 1.0 else None
    return (p_bar - p_e) / (1 - p_e)
