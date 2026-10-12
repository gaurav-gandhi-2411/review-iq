from __future__ import annotations

import pytest
from engine.intent.aux_domain import build_aux_labels, merge_aux
from engine.labelling import fooddelivery_aux as FD


def _data() -> dict:
    return {
        "classes": {"broad_intent": ["delivery", "other"]},
        "labels": {s: {"broad_intent": {}} for s in ("train", "val", "test")},
        "ids": {"train": ["t1"], "val": ["v1"], "test": ["s1"]},
    }


def _man(**over: str) -> dict:
    row = {"id": "fd-0", "domain": FD.DOMAIN, "split": FD.SPLIT, "sha": "x"} | over
    return {"items": [row]}


def test_merge_include_adds_to_train_only() -> None:
    d, t = merge_aux(
        _data(), {}, _man(), {"broad_intent": {"fd-0": "delivery"}}, {"fd-0": "late"}, True
    )
    assert (
        d["ids"]["train"] == ["t1", "fd-0"]
        and d["ids"]["val"] == ["v1"]
        and d["ids"]["test"] == ["s1"]
    )
    assert d["labels"]["train"]["broad_intent"] == {"fd-0": "delivery"}
    assert d["labels"]["val"]["broad_intent"] == {} and t == {"fd-0": "late"}


def test_merge_exclude_changes_nothing() -> None:
    d, t = merge_aux(
        _data(), {}, _man(), {"broad_intent": {"fd-0": "delivery"}}, {"fd-0": "x"}, False
    )
    assert d["ids"]["train"] == ["t1"] and t == {}


def test_merge_rejects_wrong_domain_or_val_collision() -> None:
    with pytest.raises(ValueError, match="not domain"):
        merge_aux(_data(), {}, _man(domain="other"), {}, {}, False)
    with pytest.raises(ValueError, match="not domain"):
        merge_aux(_data(), {}, _man(split="val"), {}, {}, True)
    data = _data()
    data["ids"]["val"] = ["fd-0"]
    with pytest.raises(ValueError, match="val split"):
        merge_aux(data, {}, _man(), {}, {}, True)


def test_build_aux_labels_two_judge_rule() -> None:
    def rec(v: str) -> dict:
        t = {"needs_action": "no", "broad_intent": v, "sentiment": "neg", "urgency": "low"}
        return {"id": "fd-0", "text": {**t, "aspects": {}}}

    items = {"fd-0": {"llama31_8b": rec("delivery"), "qwen3_8b": rec("delivery")}}
    man = {"items": [{"id": "fd-0", "stratum": "food_delivery_app"}]}
    assert build_aux_labels(man, items)["broad_intent"] == {"fd-0": "delivery"}
    with pytest.raises(ValueError):
        build_aux_labels({"items": [{"id": "zz", "stratum": "food_delivery_app"}]}, items)
