from __future__ import annotations

import pytest
from benchmark.tasks.language import LABELS, PRIMARY_METRIC, TASK_ID, score


def test_task_constants() -> None:
    assert TASK_ID == "LANG"
    assert set(LABELS) == {"en", "hi", "hi-en"}
    assert PRIMARY_METRIC == "accuracy"


def test_perfect_score() -> None:
    gold = ["en", "hi", "hi-en", "en", "hi"]
    result = score(gold, gold)
    assert result.task_id == "LANG"
    assert result.accuracy == pytest.approx(1.0)
    assert result.macro_f1 == pytest.approx(1.0)
    assert result.n_samples == 5


def test_hindi_vs_hinglish_confusion() -> None:
    # Common failure mode: confusing hi-en with en
    gold = ["hi-en", "hi-en", "en", "hi"]
    pred = ["en", "en", "en", "hi"]
    result = score(gold, pred)
    # Only "en" and "hi" correct out of 4
    assert result.accuracy == pytest.approx(0.5)
    # hi-en gets f1=0 (never predicted correctly)
    assert result.per_class["hi-en"].f1 == pytest.approx(0.0)


def test_majority_baseline_all_en() -> None:
    # English-majority dataset baseline: predict "en" always
    gold = ["en", "en", "hi", "hi-en"]
    pred = ["en", "en", "en", "en"]
    result = score(gold, pred)
    assert result.accuracy == pytest.approx(0.5)
    # hi and hi-en get recall=0 → macro_f1 penalised
    assert result.macro_f1 < result.accuracy


def test_labels_tuple_ordering() -> None:
    # confusion matrix rows/cols follow LABELS order
    gold = ["en", "hi", "hi-en"]
    result = score(gold, gold)
    # diagonal should be 1, off-diagonal 0
    for i in range(3):
        for j in range(3):
            expected = 1 if i == j else 0
            assert result.confusion[i][j] == expected


def test_per_class_keys() -> None:
    gold = ["en", "hi", "hi-en"]
    result = score(gold, gold)
    assert set(result.per_class.keys()) == {"en", "hi", "hi-en"}
