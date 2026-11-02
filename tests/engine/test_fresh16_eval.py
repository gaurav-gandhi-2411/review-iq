"""Amendment 16 criteria E1 to E6 on synthetic logits: estimation sets, thresholds of the verdict, and the insufficient-data rule."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip(
    "onnxruntime"
)  # the module imports the serving runtime; the CI venv skips the serving extras together
from app.core.classify import runtime  # noqa: E402
from engine.intent import fresh16_eval as E  # noqa: E402

CLASSES = {
    "needs_action": ["yes", "no"],
    "broad_intent": ["a", "b"],
    "sentiment": ["positive", "negative"],
    "urgency": ["low", "high"],
}


def thresholds(tmp_path: Path, name: str = "th.json") -> runtime.Thresholds:
    doc = {
        "schema": 1, "run": "t", "variant": "t", "max_len": 128, "head_outputs": 8, "decision": {"mode": "threshold"}, "fitted_on": "validation only",
        "tasks": {t: {"classes": c, "temperature": 1.0, "rare": {}, "abstain_below": 0.0, "n_validation_labelled": 1} for t, c in CLASSES.items()},
    }  # fmt: skip
    p = tmp_path / name
    p.write_text(json.dumps(doc), encoding="utf-8")
    return runtime.load_thresholds(p)


def logits_for(routes: list[bool]) -> np.ndarray:
    """8 head columns: needs_action(2) broad_intent(2) sentiment(2) urgency(2). A routed item says needs_action=yes; the rest say no with urgency low."""
    lg = np.zeros((len(routes), 8))
    for i, r in enumerate(routes):
        lg[i, 0:2] = [5, 0] if r else [0, 5]
        lg[i, 2:4] = [3, 0]
        lg[i, 4:6] = [3, 0]
        lg[i, 6:8] = [3, 0]
    return lg


def make_items() -> list[dict]:
    items = []
    for b, n in (("A", 40), ("B", 40), ("C", 20)):
        for k in range(n):
            items.append(
                {
                    "id": f"fresh16-{b}-food-{k:04d}",
                    "block": b,
                    "category": "food",
                    "stars": 5.0 if k % 2 == 0 else 2.0,
                    "ntok": 200 if (b == "C" or k % 4 == 0) else 50,
                }
            )
    return items


def labels_for(items: list[dict], yes: set[str]) -> dict:
    lab = {t: {} for t in CLASSES}
    for r in items:
        lab["needs_action"][r["id"]] = "yes" if r["id"] in yes else "no"
        lab["broad_intent"][r["id"]] = "a"
        lab["sentiment"][r["id"]] = "positive"
        lab["urgency"][r["id"]] = "low"
    return {"classes": CLASSES, "labels": lab}


def test_estimation_sets_never_mix_blocks_selected_on_the_other_feature() -> None:
    items = make_items()
    s = E.subsets(items)
    blocks = lambda idx: {items[k]["block"] for k in idx}  # noqa: E731
    assert (
        blocks(s["A"]) == {"A"} and blocks(s["S4"]) == {"A", "B"} and blocks(s["SL"]) == {"A", "C"}
    )
    assert all(items[k]["stars"] >= 4 for k in s["S4"]) and all(
        items[k]["ntok"] > 128 for k in s["SL"]
    )
    assert not any(items[k]["block"] == "C" for k in s["S4"]) and not any(
        items[k]["block"] == "B" for k in s["SL"]
    )


def run(
    tmp_path: Path, yes: set[str], routed4: set[str], routed2: set[str], llm_yes: set[str]
) -> dict:
    items = make_items()
    th = thresholds(tmp_path)
    ids = [r["id"] for r in items]
    llm = {i: {"ans": {"needs_action": {"label": "yes" if i in llm_yes else "no"}}} for i in ids}
    return E.evaluate(
        items,
        labels_for(items, yes),
        th,
        logits_for([i in routed4 for i in ids]),
        th,
        logits_for([i in routed2 for i in ids]),
        llm,
        n_boot=40,
    )


def test_a_model_that_routes_every_positive_meets_the_recall_criteria_when_the_sets_are_big_enough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(E, "MIN_POS", {"A": 5, "S4": 5, "SL": 3})
    items = make_items()
    yes = {
        r["id"] for r in items
    }  # every item is a gold positive and the model routes them all: 40/40, 40/40 and 30/30
    res = run(tmp_path, yes, yes, yes, set())
    assert (
        res["E1_overall_recall_block_A"]["recall_run4"]["rate"] == 1.0
        and res["E1_overall_recall_block_A"]["met"] is True
    )
    assert (
        res["E2_recall_stars_ge_4"]["met"] is True and res["E3_recall_tokens_gt_128"]["met"] is True
    )
    assert (
        res["E4_routed_share_block_A"]["met"] is False
    )  # everything routed: share 1.0, far above 0.30
    assert res["E6_non_inferior_to_llm_block_A"]["met"] is True  # the LLM caught nothing
    assert res["verdict"]["criteria_met"] is False and res["verdict"]["failing"] == ["E4"]


def test_too_few_positives_is_insufficient_data_not_a_pass_or_a_fail(tmp_path: Path) -> None:
    items = make_items()
    yes = {items[0]["id"], items[1]["id"]}
    res = run(tmp_path, yes, yes, yes, set())
    assert (
        res["E1_overall_recall_block_A"]["insufficient_data"] is True
        and res["E1_overall_recall_block_A"]["met"] is None
    )
    assert "E1" in res["verdict"]["insufficient"] and res["verdict"]["criteria_met"] is False


def test_missed_positives_fail_the_lower_bound_and_the_non_inferiority_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(E, "MIN_POS", {"A": 5, "S4": 5, "SL": 3})
    items = make_items()
    yes = {r["id"] for r in items if r["block"] == "A"}
    routed4 = {i for k, i in enumerate(sorted(yes)) if k % 2 == 0}  # half of the positives
    res = run(tmp_path, yes, routed4, yes, llm_yes=yes)  # the LLM caught all of them
    assert res["E1_overall_recall_block_A"]["met"] is False
    assert res["E6_non_inferior_to_llm_block_A"]["met"] is False
    assert {"E1", "E6"} <= set(res["verdict"]["failing"])


def test_a_macro_f1_drop_of_more_than_two_points_fails_e5(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(E, "MIN_POS", {"A": 1, "S4": 1, "SL": 1})
    items = make_items()
    th = thresholds(tmp_path)
    ids = [r["id"] for r in items]
    lab = labels_for(items, {ids[0]})
    lg4 = logits_for([False] * len(ids))
    lg4[:, 4:6] = [
        0,
        3,
    ]  # run 4 calls everything negative while gold says positive: macro-F1 on sentiment collapses
    res = E.evaluate(items, lab, th, lg4, th, logits_for([False] * len(ids)), {}, n_boot=40)
    assert res["E5_macro_f1_run4_minus_run2_block_A"]["tasks"]["sentiment"]["met"] is False
    assert res["E5_macro_f1_run4_minus_run2_block_A"]["tasks"]["broad_intent"]["met"] is True
    assert "E5" in res["verdict"]["failing"]


def test_the_registered_constants() -> None:
    assert (E.E1_LOWER, E.E2_LOWER, E.E3_LOWER, E.E4_MAX_SHARE, E.E5_MAX_DROP, E.E6_MARGIN) == (
        0.90,
        0.85,
        0.85,
        0.30,
        0.02,
        0.05,
    )
    assert E.MIN_POS == {"A": 200, "S4": 120, "SL": 120}
