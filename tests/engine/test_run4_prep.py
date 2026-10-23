"""Amendment 16: the calibration pool is training data only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from engine.intent import run4_prep as P

LABELS = {
    "labels": {
        "train": {"needs_action": {"s1": "yes", "s2": "no"}, "sentiment": {"s3": "positive"}},
        "val": {"needs_action": {"v1": "no"}},
        "test": {"needs_action": {"t1": "yes"}},
    }
}
SCALE = {i: f"text {i}" for i in ("s1", "s2", "s3", "v1", "t1")}
MAN = {
    "items": [
        {"id": "train16-T1-food-0000", "split": "train16"},
        {"id": "train16-T1-food-0001", "split": "val16"},
    ]
}
T_TEXTS = {"train16-T1-food-0000": "added", "train16-T1-food-0001": "targeted validation"}


def test_the_pool_is_run2_train_plus_t_train_and_nothing_else() -> None:
    pool = P.calibration_pool(LABELS, SCALE, MAN, T_TEXTS)
    assert [r["id"] for r in pool] == ["s1", "s2", "s3", "train16-T1-food-0000"]
    assert all(
        r["id"] not in ("v1", "t1", "train16-T1-food-0001") for r in pool
    )  # val, test and T-val are excluded


def test_a_validation_or_test_item_in_the_training_labels_aborts() -> None:
    bad = json.loads(json.dumps(LABELS))
    bad["labels"]["train"]["needs_action"]["v1"] = "no"
    with pytest.raises(ValueError, match="validation/test"):
        P.calibration_pool(bad, SCALE, MAN, T_TEXTS)


def test_val_rows_follow_the_pred_file_order(tmp_path: Path) -> None:
    (tmp_path / "pred_needs_action.json").write_text(
        json.dumps({"splits": {"val": {"ids": ["v1", "s3"]}}}), encoding="utf-8"
    )
    assert [r["id"] for r in P.val_rows(tmp_path, SCALE)] == ["v1", "s3"]
