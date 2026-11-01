"""Amendment 16 arm selection rule (validation only), on synthetic arm summaries."""

from __future__ import annotations

import pytest

pytest.importorskip(
    "onnxruntime"
)  # the module imports the serving runtime; the CI venv skips the serving extras together
from engine.intent import run4_select as S  # noqa: E402


def arm(max_len: int, trunc: str, score: float, f1: float = 0.80, routed: float = 0.27) -> dict:
    return {
        "max_len": max_len,
        "truncation": trunc,
        "score": score,
        "f1": f1,
        "routed_share_val": routed,
    }


def test_highest_score_wins_among_eligible_arms() -> None:
    arms = {
        "H128": arm(128, "head", 0.90),
        "H256": arm(256, "head", 0.95),
        "H512": arm(512, "head", 0.97),
    }
    r = S.select(arms)
    assert r["chosen"] == "H512" and r["ranking"] == ["H512", "H256", "H128"]


def test_arms_within_one_point_of_the_top_prefer_the_shorter_length_then_head() -> None:
    arms = {
        "H512": arm(512, "head", 0.960),
        "H256": arm(256, "head", 0.955),
        "HT256": arm(256, "head_tail", 0.962),
        "H128": arm(128, "head", 0.940),
    }
    r = S.select(arms)
    assert (
        r["chosen"] == "H256"
    )  # 0.955 and 0.962 and 0.960 are within 0.01 of the top 0.962; shortest is 256; head before head_tail
    assert r["ranking"][0] == "H256" and r["ranking"][-1] == "H128"


def test_an_arm_that_routes_too_much_is_not_eligible_however_good_its_recall() -> None:
    arms = {"H128": arm(128, "head", 0.90), "HT256": arm(256, "head_tail", 0.99, routed=0.31)}
    r = S.select(arms)
    assert r["chosen"] == "H128" and r["eligible"] == ["H128"]


def test_an_arm_more_than_a_point_below_the_best_macro_f1_is_not_eligible() -> None:
    arms = {"H128": arm(128, "head", 0.90, f1=0.80), "H256": arm(256, "head", 0.99, f1=0.789)}
    assert S.select(arms)["chosen"] == "H128"
    arms["H256"]["f1"] = 0.791
    assert S.select(arms)["chosen"] == "H256"


def test_the_boundaries_are_inclusive_where_the_rule_says_so() -> None:
    arms = {"A": arm(128, "head", 0.90, routed=0.30), "B": arm(256, "head", 0.90, f1=0.79)}
    r = S.select(arms)
    assert r["eligible"] == [
        "A",
        "B",
    ]  # routed share exactly 0.30 and f1 exactly 0.01 below the best both pass


def test_no_eligible_arm_means_stop_not_a_default() -> None:
    r = S.select({"H128": arm(128, "head", 0.9, routed=0.5)})
    assert r["chosen"] is None and r["ranking"] == [] and "stop" in r["reason"]
    assert S.select({})["chosen"] is None


def test_the_registered_constants() -> None:
    assert (S.F1_TOLERANCE, S.SCORE_TIE, S.MAX_ROUTED_SHARE) == (0.01, 0.01, 0.30)
