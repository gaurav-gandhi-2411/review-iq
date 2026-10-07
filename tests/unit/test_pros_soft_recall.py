"""S18 D2: `pros` soft recall (pre-registered in docs/specs/s18-pros-soft-recall.md)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from eval.pros_soft_recall import (
    DECISION_MATCH_MIN_RECALL,
    PROS_SOFT_THRESHOLD,
    V_A_MAX_SWING_POINTS,
    decision,
    phrase_tokens,
    pros_soft_precision,
    pros_soft_recall,
)

ROOT = Path(__file__).resolve().parent.parent.parent
HELD: dict[str, Any] = json.loads(
    (ROOT / "eval" / "results" / "held_out_scoring_v2.json").read_text(encoding="utf-8")
)


def test_threshold_is_the_preregistered_half() -> None:
    assert PROS_SOFT_THRESHOLD == 0.5
    assert V_A_MAX_SWING_POINTS == 5.0
    assert DECISION_MATCH_MIN_RECALL == 0.5


def test_normalisation_nfkc_case_punctuation_underscore_no_stemming_no_stopwords() -> None:
    assert phrase_tokens("Sound_Quality, GREAT!") == frozenset({"sound", "quality", "great"})
    assert phrase_tokens("ｆｕｌｌ ｐａｉｓａ") == frozenset({"full", "paisa"})  # NFKC fullwidth
    # no stemming, no stopword removal
    assert phrase_tokens("the batteries") == frozenset({"the", "batteries"})
    assert phrase_tokens("---") == frozenset()


def test_exact_and_reordered_phrase_match() -> None:
    assert pros_soft_recall(["good bass"], ["bass good"]) == 1.0


def test_jaccard_boundary_is_inclusive_at_threshold() -> None:
    # {good, bass} vs {good, bass, sound, clear}: 2/4 = 0.5 -> matched at 0.5, not at 0.6
    assert pros_soft_recall(["good bass sound clear"], ["good bass"]) == 1.0
    assert pros_soft_recall(["good bass sound clear"], ["good bass"], 0.6) == 0.0


def test_below_threshold_is_not_matched() -> None:
    # {good, bass} vs {good, bass, sound, clear, loud}: 2/5 = 0.4
    assert pros_soft_recall(["good bass sound clear loud"], ["good bass"]) == 0.0


def test_recall_is_fraction_of_gold_phrases_matched() -> None:
    gold = ["good bass", "long battery life", "value for money"]
    pred = ["bass good", "battery"]
    assert pros_soft_recall(pred, gold) == pytest.approx(1 / 3)


def test_matching_is_not_one_to_one() -> None:
    # one predicted phrase can satisfy two gold phrases
    assert pros_soft_recall(["good bass"], ["good bass", "bass good"]) == 1.0


def test_empty_gold_is_undefined_not_zero_or_one() -> None:
    assert pros_soft_recall(["good bass"], []) is None
    assert pros_soft_recall([], []) is None


def test_empty_prediction_against_nonempty_gold_is_zero() -> None:
    assert pros_soft_recall([], ["good bass"]) == 0.0


def test_empty_token_phrases_never_match() -> None:
    assert pros_soft_recall(["!!!"], ["???"]) == 0.0


def test_precision_counts_predicted_phrases_matching_gold_and_is_undefined_when_empty() -> None:
    assert pros_soft_precision(["good bass", "free gift"], ["bass good"]) == 0.5
    assert pros_soft_precision([], ["bass good"]) is None
    assert pros_soft_precision(["good bass"], []) == 0.0


def test_decision_is_binary_on_half_recall() -> None:
    assert decision(["a"], ["a", "b"]) == 1  # recall 0.5
    assert decision(["a"], ["a", "b", "c"]) == 0  # recall 1/3
    assert decision(["a"], []) is None


def test_artifact_block_is_additive_and_self_consistent() -> None:
    b = HELD["pros_soft_recall"]
    assert b["threshold"] == 0.5
    assert b["spec"] == "docs/specs/s18-pros-soft-recall.md"
    sw = b["threshold_sweep_soft_recall"]
    assert b["swing_points_between_0.4_and_0.6"] == pytest.approx(
        abs(sw["0.4"]["score"] - sw["0.6"]["score"]) * 100
    )
    assert b["validation"]["V_a"]["pass"] == (
        b["swing_points_between_0.4_and_0.6"] <= V_A_MAX_SWING_POINTS
    )
    vb = b["validation"]["V_b"]
    expect = all(
        j["concordance"] is not None and j["concordance"] >= 0.85
        for s in vb["sets"].values()
        for j in s["per_judge"].values()
    )
    assert vb["pass"] == expect
    assert b["validation"]["validated"] == (b["validation"]["V_a"]["pass"] and vb["pass"])
    assert b["n_scored"] + b["n_resolved_gold_empty_excluded"] == b["n_resolved_pros_pairs"]


def test_artifact_block_recomputes_from_records() -> None:
    from eval.score_held_out_corpus_v2 import load_quarantined_fixtures

    fx = {f["id"]: f for f in load_quarantined_fixtures()}
    vals = []
    for r in HELD["records"]:
        if r.get("exposure") or "pros" in r["unresolved_fields"]:
            continue
        v = pros_soft_recall(
            r["as_deployed"]["predicted"]["pros"], fx[r["id"]]["ground_truth"]["pros"]
        )
        if v is not None:
            vals.append(v)
    assert len(vals) == HELD["pros_soft_recall"]["n_scored"]
    assert sum(vals) / len(vals) == pytest.approx(HELD["pros_soft_recall"]["soft_recall"]["score"])
