from __future__ import annotations

import copy

import pytest
from engine.intent.safety_data import add_safety_train

CLASSES = {
    "needs_action": ("yes", "no"),
    "broad_intent": ("product_issue", "praise", "other"),
    "sentiment": ("positive", "negative"),
    "urgency": ("low", "medium", "high"),
}


def base() -> tuple[dict, dict[str, str]]:
    data = {
        "classes": CLASSES,
        "labels": {s: {t: {} for t in CLASSES} for s in ("train", "val", "test")},
        "ids": {"train": ["b1"], "val": ["b2"], "test": ["b3"]},
    }
    data["labels"]["train"]["urgency"]["b1"] = "low"
    return data, {"b1": "x", "b2": "y", "b3": "z"}


def safety() -> tuple[dict, list[dict], list[dict]]:
    split = {"items": [
        {"id": "s-tr", "sha": "h1", "split": "train"},
        {"id": "s-tr2", "sha": "h2", "split": "train"},
        {"id": "s-va", "sha": "h3", "split": "val"},
        {"id": "s-te", "sha": "h4", "split": "test"},
        {"id": "s-dr", "sha": "h5", "split": "dropped"},
    ]}  # fmt: skip
    labels = [
        {"id": "s-tr", "sha": "h1", "needs_action": "yes", "broad_intent": "product_issue", "sentiment": "negative", "urgency": "high"},
        {"id": "s-tr2", "sha": "h2", "needs_action": None, "broad_intent": "other", "sentiment": None, "urgency": None},
        {"id": "s-va", "sha": "h3", "needs_action": "no", "broad_intent": "praise", "sentiment": None, "urgency": "low"},
    ]  # fmt: skip
    texts = [{"id": r["id"], "sha": r["sha"], "text": f"text {r['id']}"} for r in split["items"]]
    return split, labels, texts


def test_adds_only_train_items_with_masked_abstentions_and_no_sentiment() -> None:
    data, texts = base()
    before = copy.deepcopy((data, texts))
    split, labels, trows = safety()
    new, ntexts, info = add_safety_train(data, texts, split, labels, trows, {"zzz"})
    assert new["ids"]["train"] == ["b1", "s-tr", "s-tr2"]
    assert new["ids"]["val"] == ["b2"] and new["ids"]["test"] == ["b3"]  # val/test untouched
    assert set(ntexts) == {"b1", "b2", "b3", "s-tr", "s-tr2"}  # no val/test/dropped safety text
    t = new["labels"]["train"]
    assert t["urgency"] == {"b1": "low", "s-tr": "high"}  # abstention left out of this task only
    assert t["broad_intent"] == {"s-tr": "product_issue", "s-tr2": "other"}
    assert t["sentiment"] == {} and "s-tr2" not in t["needs_action"]  # sentiment never taught by safety
    assert info["n_added_train"] == 2 and info["n_urgency_high"] == 1
    assert (data, texts) == before  # inputs not modified


@pytest.mark.parametrize("bad", ["h1", "h2"])
def test_refuses_a_sealed_hash(bad: str) -> None:
    data, texts = base()
    split, labels, trows = safety()
    with pytest.raises(ValueError, match="sealed hash"):
        add_safety_train(data, texts, split, labels, trows, {bad})


def test_refuses_train_item_that_is_also_val_or_test() -> None:
    data, texts = base()
    split, labels, trows = safety()
    split["items"].append({"id": "s-tr", "sha": "h1", "split": "test"})  # same id in two splits
    with pytest.raises(ValueError, match="val/test/dropped"):
        add_safety_train(data, texts, split, labels, trows, set())
    split, labels, trows = safety()
    split["items"][0]["sha"] = "h4"  # a train id carrying a test hash
    labels[0]["sha"] = trows[0]["sha"] = "h4"
    with pytest.raises(ValueError, match="val/test/dropped"):
        add_safety_train(data, texts, split, labels, trows, set())


def test_refuses_id_collision_missing_text_hash_mismatch_and_unknown_class() -> None:
    data, texts = base()
    data["ids"]["train"].append("s-tr")
    split, labels, trows = safety()
    with pytest.raises(ValueError, match="already present"):
        add_safety_train(data, texts, split, labels, trows, set())
    data, texts = base()
    trows[0]["sha"] = "other"
    with pytest.raises(ValueError, match="text missing or hash differs"):
        add_safety_train(data, texts, split, labels, trows, set())
    split, labels, trows = safety()
    labels[0]["urgency"] = "critical"
    with pytest.raises(ValueError, match="not a class"):
        add_safety_train(data, texts, split, labels, trows, set())
