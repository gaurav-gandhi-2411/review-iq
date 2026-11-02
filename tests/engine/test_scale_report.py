from __future__ import annotations

import json
import math

import pytest
from engine.labelling import scale_report as SR


def test_gwet_ac1_hand_computed_and_degenerate_cases() -> None:
    units = [["no", "no"]] * 8 + [["yes", "yes"], ["no", "yes"]]
    # pa = 0.9, pi_no = 0.85, pe = 2 * 0.85 * 0.15 = 0.255 -> (0.9 - 0.255) / 0.745
    assert SR.gwet_ac1(units) == pytest.approx(0.645 / 0.745)
    assert SR.gwet_ac1([["a", "a"], ["a", "a"]]) == 1.0  # one category used
    assert math.isnan(SR.gwet_ac1([["a", None], [None, None]]))  # no unit with two ratings
    # missing values are tolerated: the third unit has only two valid ratings
    assert -1 <= SR.gwet_ac1([["a", "a", "b"], ["b", "b", "b"], ["a", "b", None]]) <= 1


def test_class_table_flags_insufficient_data_and_wilson() -> None:
    labels = ["no"] * 70 + ["yes"] * 25 + [None] * 5
    t = SR.class_table(labels, ["no", "yes", "rare"])
    assert t["no_consensus"] == 5 and t["consensus_fraction"] == 0.95
    assert t["classes"]["yes"]["insufficient_data"] is False  # 25 >= 20
    assert t["classes"]["rare"] == {
        "n": 0, "share_of_consensus": 0.0, "wilson95": t["classes"]["rare"]["wilson95"],
        "insufficient_data": True,
    }  # fmt: skip
    lo, hi = t["classes"]["yes"]["wilson95"]
    assert lo < 25 / 95 < hi


def _rec(i: str, judge: str, **kw: str) -> dict:
    base = {
        "needs_action": "no",
        "broad_intent": "praise",
        "sentiment": "positive",
        "urgency": "low",
    }
    base.update(kw)
    return {"id": i, "judge": judge, "stratum": "food", "category": "food",
            "text": {**base, "aspects": {}}}  # fmt: skip


def _fixture(n: int = 40) -> tuple[dict, dict]:
    """40 sealed items; judges agree except: gemma flips urgency on every 4th item, mistral fails to parse on item 7."""
    manifest = {"items": [{"id": f"s{k}", "split": "test", "stratum": "food", "sha": "x"} for k in range(n)]
                + [{"id": "t0", "split": "train", "stratum": "food", "sha": "y"}]}  # fmt: skip
    items: dict = {}
    for k in range(n):
        i = f"s{k}"
        yes = k < 22  # 22 'yes' positives: above the 20 floor
        kw = {
            "needs_action": "yes" if yes else "no",
            "broad_intent": "product_issue" if yes else "praise",
        }
        recs = {j: _rec(i, j, **kw) for j in SR.PANEL}
        if k % 4 == 0:
            recs["gemma2_9b"] = _rec(i, "gemma2_9b", urgency="high", **kw)
        if k == 7:
            recs["mistral_7b"] = {"id": i, "judge": "mistral_7b", "text": None}
        items[i] = recs
    items["t0"] = {
        j: _rec("t0", j) for j in SR.PANEL
    }  # a training item must not leak into the report
    return manifest, items


def test_report_on_synthetic_sealed_set() -> None:
    manifest, items = _fixture()
    rep = SR.build_report(manifest, items)
    assert rep["n_sealed"] == 40  # the train item is excluded
    c1 = rep["tasks"]["C1_needs_action"]
    assert c1["alpha"] == 1.0 and c1["gate"] == "PASS"
    assert (
        c1["supplementary"]["gwet_ac1"] == 1.0
        and c1["supplementary"]["raw_percent_agreement"] == 100.0
    )
    assert c1["consensus"]["classes"]["yes"]["n"] == 22
    assert c1["consensus"]["classes"]["yes"]["insufficient_data"] is False
    t3 = rep["tasks"]["T3_urgency"]
    assert t3["consensus"]["consensus_fraction"] == 1.0  # one dissenter of four is still 3 of 4
    assert t3["alpha"] < 1.0 and t3["supplementary"]["raw_percent_agreement"] < 100.0
    assert "n_units" in rep["tasks"]["C2_broad_intent"]
    cons = rep["consistency_two_judge_vs_four_judge"]
    assert "not independent" in cons["caveat"]
    assert cons["needs_action"]["both_labelled"] == 40 and cons["needs_action"]["agreement"] == 1.0
    # urgency: llama and qwen agree everywhere, so the two-judge label is 'low' and matches the 3-of-4 consensus
    assert cons["urgency"]["agreement"] == 1.0
    json.dumps(
        SR._nan_to_none(rep), allow_nan=False
    )  # serialisable once undefined values are mapped to null


def test_c1_no_class_is_flagged_below_twenty() -> None:
    manifest, items = _fixture()
    rep = SR.build_report(manifest, items)
    assert rep["tasks"]["C1_needs_action"]["consensus"]["classes"]["no"]["n"] == 18
    assert (
        rep["tasks"]["C1_needs_action"]["consensus"]["classes"]["no"]["insufficient_data"] is True
    )


def test_consistency_detects_a_two_judge_rule_that_disagrees_with_the_panel() -> None:
    manifest, items = _fixture(10)
    for k in range(
        10
    ):  # llama and qwen agree on 'high'; gemma and mistral say 'low' -> panel has no majority
        i = f"s{k}"
        items[i]["llama31_8b"] = _rec(i, "llama31_8b", urgency="high")
        items[i]["qwen3_8b"] = _rec(i, "qwen3_8b", urgency="high")
        items[i]["gemma2_9b"] = _rec(i, "gemma2_9b", urgency="low")
        items[i]["mistral_7b"] = _rec(i, "mistral_7b", urgency="low")
    cons = SR.build_report(manifest, items)["consistency_two_judge_vs_four_judge"]["urgency"]
    assert cons["two_judge_only"] == 10 and cons["both_labelled"] == 0 and cons["agreement"] is None


def test_report_refuses_missing_sealed_records_or_judges() -> None:
    manifest, items = _fixture()
    del items["s3"]
    with pytest.raises(ValueError):
        SR.build_report(manifest, items)
    manifest, items = _fixture()
    for recs in items.values():
        recs.pop("gemma2_9b")
    with pytest.raises(ValueError):
        SR.build_report(manifest, items)
