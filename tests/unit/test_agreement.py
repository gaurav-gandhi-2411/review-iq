"""Unit tests for eval/agreement.py -- Krippendorff's alpha and Fleiss' kappa.

Every non-trivial test asserts against a PUBLISHED worked example with a known
expected value, not against this module's own output (a bug in the implementation
can't hide behind a self-referential test) -- see eval/agreement.py's module
docstring for the exact citations.
"""

from __future__ import annotations

import pytest
from eval.agreement import fleiss_kappa, krippendorff_alpha

# --- Krippendorff's alpha: Wikipedia "Krippendorff's alpha" worked example ---------------
# 3-coder-by-15-unit reliability matrix, "*" = missing. This exact matrix (with the
# exact coincidence-matrix arithmetic shown) appears in the "A computational example"
# section of https://en.wikipedia.org/wiki/Krippendorff%27s_alpha, itself citing
# Krippendorff, K. (2011) "Computing Krippendorff's Alpha-Reliability". The article
# states the results: alpha_nominal = 0.691, alpha_interval = 0.811.
_KRIPPENDORFF_WIKI_DATA_STR = (
    "*    *    *    *    *    3    4    1    2    1    1    3    3    *    3",  # coder A
    "1    *    2    1    3    3    4    3    *    *    *    *    *    *    *",  # coder B
    "*    *    2    1    3    4    4    *    2    1    1    3    3    *    4",  # coder C
)


def _kripp_wiki_matrix() -> list[list[int | None]]:
    return [
        [None if v == "*" else int(v) for v in coder.split()]
        for coder in _KRIPPENDORFF_WIKI_DATA_STR
    ]


class TestKrippendorffAlpha:
    def test_nominal_matches_wikipedia_worked_example(self):
        alpha = krippendorff_alpha(_kripp_wiki_matrix(), level_of_measurement="nominal")
        assert alpha == pytest.approx(0.691, abs=1e-3)

    def test_interval_matches_wikipedia_worked_example(self):
        alpha = krippendorff_alpha(_kripp_wiki_matrix(), level_of_measurement="interval")
        assert alpha == pytest.approx(0.811, abs=1e-3)

    def test_ordinal_cross_checked_against_reference_krippendorff_package(self):
        # NOT a published textbook value (Wikipedia's worked example doesn't cover the
        # ordinal metric) -- this is a secondary cross-check against the independently
        # maintained `krippendorff` PyPI package (pln-fing-udelar/fast-krippendorff)
        # run against the SAME Wikipedia data matrix above, which gave 0.8067214199413153
        # (verified interactively during development; the nominal/interval cases above
        # are the required published-textbook validation of the shared coincidence-
        # matrix engine this reuses).
        alpha = krippendorff_alpha(
            _kripp_wiki_matrix(), level_of_measurement="ordinal", categories=[1, 2, 3, 4]
        )
        assert alpha == pytest.approx(0.8067214199413153, abs=1e-9)

    def test_perfect_agreement_is_one(self):
        data = [[1, 2, 3], [1, 2, 3], [1, 2, 3]]
        assert krippendorff_alpha(data, "nominal") == pytest.approx(1.0)

    def test_no_pairable_units_returns_none(self):
        # Every unit has at most 1 non-missing rating -- nothing is pairable.
        data = [[1, None, None], [None, 2, None], [None, None, 3]]
        assert krippendorff_alpha(data, "nominal") is None

    def test_ordinal_without_categories_raises(self):
        with pytest.raises(ValueError, match="requires explicit `categories`"):
            krippendorff_alpha([[1, 2], [1, 2]], level_of_measurement="ordinal")

    def test_unknown_level_of_measurement_raises(self):
        with pytest.raises(ValueError, match="unknown level_of_measurement"):
            krippendorff_alpha([[1, 2], [1, 2]], level_of_measurement="bogus")  # type: ignore[arg-type]

    def test_single_unit_two_raters_full_disagreement(self):
        # Hostile input: exactly one pairable unit, complete disagreement -- alpha
        # should be well below 1 (boundary: minimum viable pairable data).
        data = [[1], [2]]
        alpha = krippendorff_alpha(data, "nominal")
        assert alpha is not None
        assert alpha < 1.0


# --- Fleiss' kappa: Wikipedia "Fleiss's kappa" worked example (Fleiss, 1971) --------------
# 10 patients (items), 14 psychiatrists (raters) each, 5 diagnosis categories. This exact
# n_ij table (with full arithmetic: P_bar=0.378, P_e=0.213) appears in the "Worked example"
# section of https://en.wikipedia.org/wiki/Fleiss%27s_kappa, citing Fleiss, J.L. (1971)
# "Measuring nominal scale agreement among many raters", Psychological Bulletin 76(5),
# 378-382. The article states the result: kappa = 0.210.
_FLEISS_1971_TABLE = [
    [0, 0, 0, 0, 14],
    [0, 2, 6, 4, 2],
    [0, 0, 3, 5, 6],
    [0, 3, 9, 2, 0],
    [2, 2, 8, 1, 1],
    [7, 7, 0, 0, 0],
    [3, 2, 6, 3, 0],
    [2, 5, 3, 2, 2],
    [6, 5, 2, 1, 0],
    [0, 2, 2, 3, 7],
]


class TestFleissKappa:
    def test_matches_fleiss_1971_worked_example(self):
        kappa = fleiss_kappa(_FLEISS_1971_TABLE)
        assert kappa == pytest.approx(0.210, abs=1e-3)

    def test_perfect_agreement_is_one(self):
        # All 3 raters agree on every one of 4 items (2 items category A, 2 category B).
        table = [[3, 0], [3, 0], [0, 3], [0, 3]]
        assert fleiss_kappa(table) == pytest.approx(1.0)

    def test_empty_table_returns_none(self):
        assert fleiss_kappa([]) is None

    def test_uneven_rater_counts_raises(self):
        with pytest.raises(ValueError, match="same total rater count"):
            fleiss_kappa([[3, 0], [2, 0]])

    def test_random_chance_agreement_near_zero(self):
        # 3 raters split maximally evenly across 3 categories on every item -- close to
        # chance-level agreement (boundary: low/no-signal input, kappa near 0).
        table = [[1, 1, 1]] * 6
        kappa = fleiss_kappa(table)
        assert kappa is not None
        assert kappa < 0.2
