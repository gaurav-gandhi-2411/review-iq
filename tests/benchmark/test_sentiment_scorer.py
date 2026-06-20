from __future__ import annotations

import pytest
from benchmark.tasks.sentiment import LABELS, PRIMARY_METRIC, TASK_ID, score


def test_task_constants() -> None:
    assert TASK_ID == "SENT"
    assert set(LABELS) == {"positive", "neutral", "negative"}
    assert PRIMARY_METRIC == "macro_f1"


def test_perfect_score() -> None:
    gold = ["positive", "neutral", "negative", "positive", "negative"]
    result = score(gold, gold)
    assert result.task_id == "SENT"
    assert result.n_samples == 5
    assert result.accuracy == pytest.approx(1.0)
    assert result.macro_f1 == pytest.approx(1.0)
    assert result.labels == LABELS


def test_majority_class_prediction() -> None:
    # Majority-class baseline: always predict "positive"
    gold = ["positive", "positive", "neutral", "negative", "negative"]
    pred = ["positive"] * 5
    result = score(gold, pred)
    # Accuracy = 2/5
    assert result.accuracy == pytest.approx(0.4)
    # neutral and negative get f1=0 → macro_f1 = f1_positive / 3
    assert result.macro_f1 < 0.4  # worse than accuracy on imbalanced


def test_primary_metric_accessor() -> None:
    gold = ["positive", "negative"]
    result = score(gold, gold)
    assert result.primary_metric("macro_f1") == pytest.approx(result.macro_f1)
    assert result.primary_metric("accuracy") == pytest.approx(result.accuracy)


def test_primary_metric_unknown_raises() -> None:
    result = score(["positive"], ["positive"])
    with pytest.raises(ValueError, match="Unknown metric"):
        result.primary_metric("f2")


def test_confusion_shape() -> None:
    gold = ["positive", "neutral", "negative"]
    result = score(gold, gold)
    # 3×3 matrix
    assert len(result.confusion) == 3
    assert all(len(row) == 3 for row in result.confusion)


def test_per_class_keys() -> None:
    gold = ["positive", "neutral", "negative"]
    result = score(gold, gold)
    assert set(result.per_class.keys()) == {"positive", "neutral", "negative"}


def test_length_mismatch_propagates() -> None:
    with pytest.raises(ValueError, match="Length mismatch"):
        score(["positive", "negative"], ["positive"])
