"""Amendment 16 labels (four-judge consensus) and the run-4 training additions."""

from __future__ import annotations

import pytest
from engine.intent import fresh16_data as D
from engine.intent import labels as L
from engine.labelling import schema as S

JUDGES = ("llama31_8b", "qwen3_8b", "gemma2_9b", "mistral_7b")


def rec(
    na: str,
    intent: str = "other",
    sent: str = "neutral",
    urg: str = "low",
    aspects: dict | None = None,
) -> dict:
    return {
        "text": {
            "needs_action": na,
            "broad_intent": intent,
            "sentiment": sent,
            "urgency": urg,
            "aspects": aspects or {},
        },
        "mismatch": None,
    }


def item(votes: list[str]) -> dict:
    return {j: rec(v) for j, v in zip(JUDGES, votes, strict=True)}


MAN = {
    "items": [
        {"id": "fresh16-A-food-0000", "category": "food"},
        {"id": "fresh16-A-food-0001", "category": "food"},
    ]
}


def test_the_labels_are_the_three_of_four_consensus_and_split_votes_abstain() -> None:
    items = {
        "fresh16-A-food-0000": item(["yes", "yes", "yes", "no"]),
        "fresh16-A-food-0001": item(["yes", "yes", "no", "no"]),
    }
    out = D.build_labels(MAN, items)
    assert out["labels"]["needs_action"] == {
        "fresh16-A-food-0000": "yes"
    }  # 2-2 has no consensus: left out of this task only
    assert out["labels"]["sentiment"] == {
        "fresh16-A-food-0000": "neutral",
        "fresh16-A-food-0001": "neutral",
    }
    assert set(out["classes"]) == set(L.task_classes())


def test_an_item_with_no_judge_record_is_an_error() -> None:
    with pytest.raises(ValueError, match="no judge record"):
        D.build_labels(MAN, {"fresh16-A-food-0000": item(["yes"] * 4)})


def _base() -> dict:
    classes = {t: list(c) for t, c in L.task_classes().items()}
    return {
        "classes": classes,
        "labels": {"train": {t: {} for t in classes}},
        "ids": {"train": ["scale-0"], "val": [], "test": []},
    }


def _train_manifest() -> dict:
    return {"items": [
        {"id": "train16-T1-food-0000", "sha": "a1", "split": "train16"},
        {"id": "train16-T1-food-0001", "sha": "a2", "split": "val16"},
    ]}  # fmt: skip


def _labels16() -> dict:
    cl = {t: list(c) for t, c in L.task_classes().items()}
    lab = {t: {} for t in cl}
    lab["needs_action"] = {"train16-T1-food-0000": "yes", "train16-T1-food-0001": "yes"}
    lab["sentiment"] = {"train16-T1-food-0000": "mixed"}
    return {"classes": cl, "labels": lab}


ROWS = [
    {"id": "train16-T1-food-0000", "sha": "a1", "text": "t0"},
    {"id": "train16-T1-food-0001", "sha": "a2", "text": "t1"},
]


def test_t_train_joins_the_train_split_and_t_val_is_returned_separately() -> None:
    data, texts, extra = D.add_train16(
        _base(), {"scale-0": "x"}, _train_manifest(), _labels16(), ROWS, sealed_hashes=set()
    )
    assert (
        data["ids"]["train"] == ["scale-0", "train16-T1-food-0000"]
        and texts["train16-T1-food-0000"] == "t0"
    )
    assert data["labels"]["train"]["needs_action"] == {"train16-T1-food-0000": "yes"}
    assert data["labels"]["train"]["sentiment"] == {"train16-T1-food-0000": "mixed"}
    vt = extra["val_targeted"]
    assert vt["ids"] == ["train16-T1-food-0001"] and vt["labels"]["needs_action"] == {
        "train16-T1-food-0001": "yes"
    }
    assert "train16-T1-food-0001" not in data["labels"]["train"]["needs_action"]  # never trained on
    assert extra["info"]["n_added_train"] == 1 and extra["info"]["n_gold_yes_val_targeted"] == 1


def test_inputs_are_not_modified() -> None:
    base = _base()
    D.add_train16(base, {}, _train_manifest(), _labels16(), ROWS, set())
    assert base["ids"]["train"] == ["scale-0"] and base["labels"]["train"]["needs_action"] == {}


@pytest.mark.parametrize(
    ("sealed", "rows", "match"),
    [
        ({"a1"}, ROWS, "sealed hash"),
        (
            set(),
            [{"id": "train16-T1-food-0000", "sha": "zz", "text": "t"}, ROWS[1]],
            "hash differs",
        ),
        (set(), ROWS[:1], "text missing"),
    ],
)
def test_leaks_and_missing_inputs_abort_the_run(sealed: set, rows: list, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        D.add_train16(_base(), {}, _train_manifest(), _labels16(), rows, sealed)


def test_a_wrong_split_or_a_non_class_label_aborts() -> None:
    bad = {"items": [{"id": "x", "sha": "a1", "split": "test"}]}
    with pytest.raises(ValueError, match="unexpected split"):
        D.add_train16(_base(), {}, bad, _labels16(), ROWS, set())
    lab = _labels16()
    lab["labels"]["sentiment"]["train16-T1-food-0000"] = "ecstatic"
    with pytest.raises(ValueError, match="not a class"):
        D.add_train16(_base(), {}, _train_manifest(), lab, ROWS, set())
    assert "mixed" in S.SENTIMENTS
