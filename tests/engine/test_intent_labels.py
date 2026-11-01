from __future__ import annotations

import json
from pathlib import Path

import pytest
from engine.intent import labels as L


def rec(i: str, aspects: dict | None = None, **kw: str) -> dict:
    base = {
        "needs_action": "no",
        "broad_intent": "praise",
        "sentiment": "positive",
        "urgency": "low",
    }
    base.update(kw)
    return {"id": i, "text": {**base, "aspects": aspects or {}}}


def manifest(rows: list[tuple[str, str, str]]) -> dict:
    return {"items": [{"id": i, "stratum": s, "split": sp, "sha": i} for i, s, sp in rows]}


def test_task_classes_cover_core_and_ten_aspect_tasks() -> None:
    tc = L.task_classes()
    assert tc["needs_action"] == ("yes", "no")
    assert len(tc["broad_intent"]) == 5
    assert sum(t.startswith("aspect:") for t in tc) == 10
    assert tc["aspect:food:taste"] == ("positive", "negative", "neutral")


def test_train_two_judge_rule_and_per_task_abstain() -> None:
    m = manifest([("a", "food", "train"), ("b", "food", "train")])
    items = {
        # a: judges agree on everything except urgency; mistral breaks the tie toward qwen
        "a": {
            "llama31_8b": rec("a", {"taste": "positive"}),
            "qwen3_8b": rec("a", {"taste": "positive"}, urgency="medium"),
            "mistral_7b": rec("a", urgency="medium"),
        },
        # b: sentiment three-way split -> abstains on sentiment only
        "b": {
            "llama31_8b": rec("b", sentiment="positive"),
            "qwen3_8b": rec("b", sentiment="negative"),
            "mistral_7b": rec("b", sentiment="mixed"),
        },
    }
    t = L.build_tables(m, items)["train"]
    assert t["urgency"] == {"a": "medium", "b": "low"}
    assert "b" not in t["sentiment"] and t["sentiment"]["a"] == "positive"
    assert t["aspect:food:taste"] == {"a": "positive"}  # both first-pass judges quoted it


def test_aspect_quoted_by_one_judge_or_out_of_category_is_not_a_label() -> None:
    m = manifest([("a", "beauty", "val"), ("v", "vernacular", "val")])
    items = {
        "a": {
            "llama31_8b": rec("a", {"texture": "positive", "taste": "positive"}),
            "qwen3_8b": rec("a", {"taste": "positive"}),
        },
        "v": {"llama31_8b": rec("v"), "qwen3_8b": rec("v")},
    }
    t = L.build_tables(m, items)["val"]
    assert t["aspect:beauty:texture"] == {}  # one judge only
    assert not any(t[k] for k in t if k.startswith("aspect:"))  # taste is not a beauty aspect
    assert set(t["needs_action"]) == {"a", "v"}


def test_sealed_uses_four_judge_consensus() -> None:
    m = manifest([("s", "food", "test"), ("u", "food", "test")])
    four = ("llama31_8b", "qwen3_8b", "gemma2_9b", "mistral_7b")
    items = {
        "s": {j: rec("s", {"taste": "negative"}, sentiment="negative") for j in four},
        "u": {
            j: rec("u", sentiment=s)
            for j, s in zip(four, ("positive", "positive", "negative", "negative"), strict=True)
        },
    }
    t = L.build_tables(m, items)["test"]
    assert t["sentiment"] == {"s": "negative"}  # 2-2 split is the abstain set
    assert t["aspect:food:taste"] == {"s": "negative"}
    assert t["needs_action"] == {"s": "no", "u": "no"}


def test_missing_records_raise_unless_partial() -> None:
    m = manifest([("a", "food", "train"), ("zz", "food", "train")])
    items = {"a": {"llama31_8b": rec("a"), "qwen3_8b": rec("a")}}
    with pytest.raises(ValueError, match="no judge record"):
        L.build_tables(m, items)
    assert L.build_tables(m, items, allow_partial=True)["train"]["needs_action"] == {"a": "no"}


def test_summarize_and_load_texts(tmp_path: Path) -> None:
    m = manifest([("a", "food", "train")])
    items = {"a": {"llama31_8b": rec("a"), "qwen3_8b": rec("a")}}
    s = L.summarize(m, L.build_tables(m, items))
    assert s["train"]["n_items"] == 1
    assert s["train"]["tasks"]["needs_action"]["classes"] == {"no": 1}
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"id": "a", "text": "hello"}) + "\n", encoding="utf-8")
    assert L.load_texts(p) == {"a": "hello"}
