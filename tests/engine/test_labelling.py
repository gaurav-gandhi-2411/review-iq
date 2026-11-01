from __future__ import annotations

import numpy as np
import pytest
from engine.labelling import agreement as A
from engine.labelling import prompts as P

# Wikipedia's Fleiss' kappa example: 10 subjects, 14 raters, 5 categories; published kappa = 0.210.
FLEISS_COUNTS = [
    [0, 0, 0, 0, 14], [0, 2, 6, 4, 2], [0, 0, 3, 5, 6], [0, 3, 9, 2, 0], [2, 2, 8, 1, 1],
    [7, 7, 0, 0, 0], [3, 2, 6, 3, 0], [2, 5, 3, 2, 2], [6, 5, 2, 1, 0], [0, 2, 2, 3, 7],
]  # fmt: skip


def test_fleiss_kappa_matches_the_published_example() -> None:
    units = [[c for c, n in enumerate(row) for _ in range(n)] for row in FLEISS_COUNTS]
    assert round(A.fleiss_kappa(units), 3) == 0.210


def test_alpha_is_one_for_perfect_agreement_and_not_above_chance_for_disagreement() -> None:
    perfect = [["a", "a", "a"], ["b", "b", "b"], ["a", "a", "a"], ["b", "b", "b"]]
    assert A.krippendorff_alpha(perfect, ["a", "b"]) == pytest.approx(1.0)
    split = [["a", "b"], ["b", "a"], ["a", "b"], ["b", "a"]]
    assert A.krippendorff_alpha(split, ["a", "b"]) < 0


def test_alpha_matches_the_reference_implementation_with_missing_values() -> None:
    kd = pytest.importorskip("krippendorff")
    rng = np.random.default_rng(7)
    levels = ["lo", "mid", "hi"]
    truth = rng.integers(0, 3, 120)
    data = []
    for t in truth:
        row = [int(t) if rng.random() < 0.7 else int(rng.integers(0, 3)) for _ in range(4)]
        data.append([None if rng.random() < 0.08 else levels[v] for v in row])
    for ordinal, level in ((False, "nominal"), (True, "ordinal")):
        mine = A.krippendorff_alpha(data, levels, ordinal=ordinal)
        arr = np.array([[levels.index(v) if v is not None else np.nan for v in u] for u in data]).T
        ref = kd.alpha(reliability_data=arr, level_of_measurement=level)
        assert mine == pytest.approx(ref, abs=1e-9)


def test_consensus_requires_three_matching_judges() -> None:
    r = A.consensus([["a", "a", "a", "b"], ["a", "a", "b", "b"], ["c", "c", "c", "c"]])
    assert r["clear_consensus"] == pytest.approx(2 / 3)
    assert r["unanimous"] == pytest.approx(1 / 3)


def test_gate_thresholds_are_the_pre_registered_ones() -> None:
    assert A.verdict({"alpha": 0.81, "clear_consensus": 0.9}) == "PASS"
    assert A.verdict({"alpha": 0.70, "clear_consensus": 0.9}) == "USABLE-WITH-CAVEAT"
    assert A.verdict({"alpha": 0.90, "clear_consensus": 0.60}) == "FAIL"
    assert A.verdict({"alpha": 0.60, "clear_consensus": 0.95}) == "FAIL"


def test_parse_text_is_strict_and_filters_aspects() -> None:
    good = (
        '```json\n{"primary_intent":"product_defect","secondary_intents":["return_refund_request","bogus"],'
        '"sentiment":"negative","urgency":"high","buy_again":"no","aspects":{"fit_size":"negative","nonsense":"negative"}}\n```'
    )
    out = P.parse_text(good, "apparel")
    assert out is not None
    assert out["secondary_intents"] == ["return_refund_request"]
    assert out["aspects"] == {"fit_size": "negative"}
    assert (
        P.parse_text(
            '{"primary_intent":"nonsense","sentiment":"negative","urgency":"low","buy_again":"no"}',
            "food",
        )
        is None
    )
    assert P.parse_text("not json", "food") is None
    assert P.parse_mismatch('{"mismatch": "yes"}') == "yes"
    assert P.parse_mismatch('{"mismatch": "maybe"}') is None


def test_text_prompt_never_shows_stars_and_hash_is_stable() -> None:
    p = P.text_prompt("it broke", "beauty")
    assert "star" not in p.lower()
    assert "texture" in p and "fit_size" not in p
    assert P.prompt_hash() == P.prompt_hash()
