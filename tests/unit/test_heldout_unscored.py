"""Unit tests for eval/heldout_unscored.py (Session 17, W3): the unscored fraction and the bounds
that qualify the held-out headline."""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any

import pytest
from eval.heldout_exposure import unresolved_fields
from eval.heldout_unscored import (
    judge_sensitivity,
    unscored_block,
    validate_votes_match_gold,
)

ROOT = Path(__file__).resolve().parent.parent.parent
FIELDS = ["sentiment", "pros"]


def _rec(
    rid: str, sentiment: float, pros: float, unresolved: tuple[str, ...] = (), exposed: bool = False
) -> dict[str, Any]:
    return {
        "id": rid,
        "exposure": ["benchmark_gold"] if exposed else [],
        "unresolved_fields": list(unresolved),
        "as_deployed": {
            "field_scores": {"sentiment": sentiment, "pros": pros},
            "predicted": {"sentiment": "positive", "pros": ["good"]},
        },
    }


RECORDS = [
    _rec("a", 1.0, 1.0),
    _rec("b", 1.0, 0.0, unresolved=("pros",)),  # pros is a default: its 0.0 is not a label
    _rec("c", 0.0, 0.5, unresolved=("pros", "sentiment")),  # every pair unscored
    _rec("d", 1.0, 1.0, exposed=True),  # exposed: not in the published cell
]


def test_pairs_are_reviews_times_fields_over_the_unseen_cell() -> None:
    out = unscored_block(RECORDS, FIELDS)
    assert out["n_reviews"] == 3  # `d` is exposed
    assert out["n_pairs"] == 3 * len(FIELDS)
    assert out["n_pairs_unscored"] == 3  # b.pros, c.pros, c.sentiment
    assert out["unscored_fraction"] == pytest.approx(3 / 6)
    assert out["per_field"]["pros"] == {"pairs": 3, "unscored": 2, "unscored_fraction": 2 / 3}
    assert out["per_field"]["sentiment"]["unscored"] == 1
    assert out["n_reviews_with_any_unscored_pair"] == 2
    assert out["n_reviews_with_every_pair_unscored"] == 1


def test_scored_only_equals_the_headline_definition() -> None:
    """Per-review mean over surviving fields, a fully-unscored review contributing nothing."""
    out = unscored_block(RECORDS, FIELDS)
    # a: (1+1)/2 = 1.0 ; b: sentiment only = 1.0 ; c: contributes nothing.
    assert out["scored_only"]["score"] == mean([1.0, 1.0])
    assert out["scored_only"]["ci_95"]["n"] == 2


def test_bounds_fill_unscored_pairs_with_zero_and_one() -> None:
    out = unscored_block(RECORDS, FIELDS)
    lo = out["bounds"]["lower_unscored_all_wrong"]["score"]
    hi = out["bounds"]["upper_unscored_all_correct"]["score"]
    # lower: a=1.0, b=(1+0)/2, c=(0+0)/2 ; upper: a=1.0, b=(1+1)/2, c=(1+1)/2
    assert lo == pytest.approx(mean([1.0, 0.5, 0.0]))
    assert hi == pytest.approx(mean([1.0, 1.0, 1.0]))
    assert lo <= out["scored_only"]["score"] <= hi
    # The default scored as if it were a label sits inside the interval but is NOT a bound.
    ref = out["defaults_scored_as_labels_reference"]["score"]
    assert lo <= ref <= hi


def test_per_field_bounds_collapse_when_every_pair_is_scored() -> None:
    out = unscored_block(RECORDS, FIELDS)
    sentiment = out["bounds"]["per_field"]["sentiment"]
    assert sentiment["scored_only"] == mean([1.0, 1.0])
    assert sentiment["lower_unscored_all_wrong"] == pytest.approx(2 / 3)
    assert sentiment["upper_unscored_all_correct"] == pytest.approx(1.0)
    out2 = unscored_block([_rec("x", 0.5, 0.5), _rec("y", 1.0, 0.0)], FIELDS)
    b = out2["bounds"]["per_field"]["pros"]
    assert b["lower_unscored_all_wrong"] == b["upper_unscored_all_correct"] == b["scored_only"]
    assert out2["unscored_fraction"] == 0.0


def test_a_point_estimate_is_never_claimed_identifiable() -> None:
    out = unscored_block(RECORDS, FIELDS)
    assert out["point_estimate_identifiable"] is False
    assert "Not identifiable" in out["identifiability_note"]


def test_exclude_exposed_false_counts_every_review() -> None:
    assert unscored_block(RECORDS, FIELDS, exclude_exposed=False)["n_reviews"] == 4


# ---- judge sensitivity ----------------------------------------------------------------------


def _fixture(rid: str, sentiment: str) -> dict[str, Any]:
    return {
        "id": rid,
        "ground_truth": {"sentiment": sentiment},
        "scoring_notes": {"exact_match_fields": ["sentiment"]},
    }


def test_judge_sensitivity_scores_each_unscored_pair_against_each_judge() -> None:
    rec = {
        "id": "r1",
        "exposure": [],
        "unresolved_fields": ["sentiment"],
        "as_deployed": {"field_scores": {"sentiment": 0.0}, "predicted": {"sentiment": "positive"}},
    }
    votes = {"r1": {"sentiment": {"j1": "positive", "j2": "negative", "j3": "mixed"}}}
    out = judge_sensitivity([rec], {"r1": _fixture("r1", "mixed")}, votes, ["sentiment"])
    assert out["n_unscored_pairs_scored"] == 1
    per = {j: v["score"] for j, v in out["scored_against_each_judge"].items()}
    assert per == {"j1": 1.0, "j2": 0.0, "j3": 0.0}
    assert out["envelope_min_over_judges"]["score"] == 0.0
    assert out["envelope_max_over_judges"]["score"] == 1.0
    assert "NOT verified" in out["assumption"]


def test_judge_sensitivity_leaves_scored_pairs_alone() -> None:
    rec = {
        "id": "r1",
        "exposure": [],
        "unresolved_fields": [],
        "as_deployed": {"field_scores": {"sentiment": 1.0}, "predicted": {"sentiment": "positive"}},
    }
    votes = {"r1": {"sentiment": {"j1": "negative", "j2": "negative", "j3": "negative"}}}
    out = judge_sensitivity([rec], {"r1": _fixture("r1", "positive")}, votes, ["sentiment"])
    assert out["n_unscored_pairs_scored"] == 0
    assert (
        out["envelope_min_over_judges"]["score"] == out["envelope_max_over_judges"]["score"] == 1.0
    )


def test_votes_that_do_not_match_gold_are_rejected() -> None:
    fx = {"r1": _fixture("r1", "positive")}
    bad = {"r1": {"sentiment": {"j1": "negative", "j2": "negative"}}}
    assert validate_votes_match_gold(fx, bad, {})  # no vote equals the stored gold
    assert not validate_votes_match_gold(fx, {"r1": {"sentiment": {"j1": "positive"}}}, {})
    assert validate_votes_match_gold(fx, {}, {})  # missing votes are a problem, not a pass
    # An unresolved field is exempt: its stored gold is a default, so votes need not match it.
    assert not validate_votes_match_gold(fx, bad, {"r1": {"sentiment"}})


# ---- the committed artifact -----------------------------------------------------------------


def test_committed_artifact_unscored_block_matches_the_fixtures() -> None:
    """The artifact's unscored counts re-derive from the committed fixtures and exposure."""
    artifact = json.loads(
        (ROOT / "eval" / "results" / "held_out_scoring_v2.json").read_text(encoding="utf-8")
    )
    un = artifact["unscored"]
    fixtures = {
        p.stem: json.loads(p.read_text(encoding="utf-8"))
        for p in (ROOT / "eval" / "fixtures" / "_held_out_hindi_hinglish").glob("hien-*.json")
    }
    unseen = [r for r in artifact["records"] if not r["exposure"]]
    fields = artifact["headline_fields"]
    expected = sum(len(set(unresolved_fields(fixtures[r["id"]])) & set(fields)) for r in unseen)
    assert (
        un["n_reviews"] == len(unseen) == artifact["headline_grid"]["unexposed_split_excluded"]["n"]
    )
    assert un["n_pairs"] == len(unseen) * len(fields)
    assert un["n_pairs_unscored"] == expected
    assert (
        un["n_pairs_unscored"]
        == artifact["headline_grid"]["unexposed_split_excluded"]["n_split_pairs_excluded"]
    )
    assert un["scored_only"]["score"] == pytest.approx(
        artifact["overall_score_headline"]["as_deployed"]
    )
    assert un["bounds"]["lower_unscored_all_wrong"]["score"] <= un["scored_only"]["score"]
    assert un["scored_only"]["score"] <= un["bounds"]["upper_unscored_all_correct"]["score"]
