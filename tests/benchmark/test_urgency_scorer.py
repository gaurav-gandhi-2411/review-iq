from __future__ import annotations

import pytest
from benchmark.tasks.urgency import LABELS, PRIMARY_METRIC, TASK_ID, score


def test_task_constants() -> None:
    assert TASK_ID == "URG"
    assert set(LABELS) == {"low", "medium", "high"}
    assert PRIMARY_METRIC == "macro_f1"


def test_perfect_score() -> None:
    gold = ["low", "medium", "high", "low", "high"]
    result = score(gold, gold)
    assert result.task_id == "URG"
    assert result.accuracy == pytest.approx(1.0)
    assert result.macro_f1 == pytest.approx(1.0)
    assert result.n_samples == 5


def test_majority_class_all_low() -> None:
    # Urgency skews heavily low; majority-class baseline predicts "low"
    gold = ["low", "low", "low", "medium", "high"]
    pred = ["low", "low", "low", "low", "low"]
    result = score(gold, pred)
    # Accuracy = 3/5
    assert result.accuracy == pytest.approx(0.6)
    # medium and high get f1=0 → macro_f1 well below accuracy
    assert result.per_class["medium"].f1 == pytest.approx(0.0)
    assert result.per_class["high"].f1 == pytest.approx(0.0)
    assert result.macro_f1 < 0.4


def test_high_urgency_recall_critical() -> None:
    # Detect that a system missing high-urgency cases is penalised
    # — high urgency recall=0.0 drags macro_f1 down significantly
    gold = ["high", "high", "medium", "low", "low"]
    pred = ["medium", "low", "medium", "low", "low"]  # never predicts high
    result = score(gold, pred)
    assert result.per_class["high"].recall == pytest.approx(0.0)
    assert result.per_class["high"].f1 == pytest.approx(0.0)
    assert result.macro_f1 < 0.5


def test_label_ordering_in_confusion() -> None:
    # LABELS order: low=0, medium=1, high=2
    gold = ["low", "medium", "high"]
    result = score(gold, gold)
    assert result.confusion[0][0] == 1  # low predicted as low
    assert result.confusion[1][1] == 1  # medium predicted as medium
    assert result.confusion[2][2] == 1  # high predicted as high
    assert result.confusion[0][2] == 0  # low never predicted as high


def test_per_class_support_counts() -> None:
    gold = ["low", "low", "medium", "high"]
    result = score(gold, gold)
    assert result.per_class["low"].support == 2
    assert result.per_class["medium"].support == 1
    assert result.per_class["high"].support == 1
