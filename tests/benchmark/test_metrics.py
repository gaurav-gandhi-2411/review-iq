from __future__ import annotations

import pytest
from benchmark.tasks._metrics import accuracy, confusion_matrix, macro_f1, per_class_metrics

LABELS_3 = ["positive", "neutral", "negative"]


# ---------------------------------------------------------------------------
# _validate guards
# ---------------------------------------------------------------------------


def test_validate_length_mismatch() -> None:
    with pytest.raises(ValueError, match="Length mismatch"):
        accuracy(["positive", "negative"], ["positive"])


def test_validate_empty_raises() -> None:
    with pytest.raises(ValueError, match="Empty"):
        accuracy([], [])


# ---------------------------------------------------------------------------
# accuracy
# ---------------------------------------------------------------------------


def test_accuracy_perfect() -> None:
    gold = ["positive", "neutral", "negative"]
    assert accuracy(gold, gold) == pytest.approx(1.0)


def test_accuracy_all_wrong() -> None:
    gold = ["positive", "positive", "positive"]
    pred = ["negative", "negative", "negative"]
    assert accuracy(gold, pred) == pytest.approx(0.0)


def test_accuracy_partial() -> None:
    gold = ["positive", "positive", "negative", "negative"]
    pred = ["positive", "negative", "negative", "negative"]
    # 3 correct out of 4
    assert accuracy(gold, pred) == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# per_class_metrics
# ---------------------------------------------------------------------------


def test_per_class_metrics_perfect() -> None:
    gold = ["positive", "neutral", "negative"]
    pc = per_class_metrics(gold, gold, LABELS_3)
    for lbl in LABELS_3:
        assert pc[lbl].precision == pytest.approx(1.0)
        assert pc[lbl].recall == pytest.approx(1.0)
        assert pc[lbl].f1 == pytest.approx(1.0)
        assert pc[lbl].support == 1


def test_per_class_metrics_all_predicted_positive() -> None:
    # Predict "positive" for everything — positive precision=0.33, recall=1.0
    gold = ["positive", "neutral", "negative"]
    pred = ["positive", "positive", "positive"]
    pc = per_class_metrics(gold, pred, LABELS_3)
    # positive: tp=1, fp=2, fn=0
    assert pc["positive"].precision == pytest.approx(1 / 3)
    assert pc["positive"].recall == pytest.approx(1.0)
    assert pc["positive"].f1 == pytest.approx(0.5)
    # neutral: tp=0, fp=0, fn=1 → prec=0 (no pos pred), rec=0, f1=0
    assert pc["neutral"].precision == pytest.approx(0.0)
    assert pc["neutral"].recall == pytest.approx(0.0)
    assert pc["neutral"].f1 == pytest.approx(0.0)


def test_per_class_metrics_zero_support_class() -> None:
    # "neutral" never appears in gold → support=0, prec=0 (no pred), rec=0, f1=0
    gold = ["positive", "positive", "negative"]
    pred = ["positive", "negative", "negative"]
    pc = per_class_metrics(gold, pred, LABELS_3)
    assert pc["neutral"].support == 0
    assert pc["neutral"].f1 == pytest.approx(0.0)


def test_per_class_metrics_known_case() -> None:
    # gold: [pos, pos, neg, neg, neu]
    # pred: [pos, neg, neg, pos, neu]
    # positive: tp=1(idx0), fp=1(idx3), fn=1(idx1) → prec=0.5, rec=0.5, f1=0.5
    # negative: tp=1(idx2), fp=1(idx1), fn=1(idx3) → prec=0.5, rec=0.5, f1=0.5
    # neutral:  tp=1(idx4), fp=0,       fn=0        → prec=1.0, rec=1.0, f1=1.0
    gold = ["positive", "positive", "negative", "negative", "neutral"]
    pred = ["positive", "negative", "negative", "positive", "neutral"]
    pc = per_class_metrics(gold, pred, LABELS_3)
    assert pc["positive"].f1 == pytest.approx(0.5)
    assert pc["negative"].f1 == pytest.approx(0.5)
    assert pc["neutral"].f1 == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# macro_f1
# ---------------------------------------------------------------------------


def test_macro_f1_perfect() -> None:
    gold = ["positive", "neutral", "negative"]
    assert macro_f1(gold, gold, LABELS_3) == pytest.approx(1.0)


def test_macro_f1_all_one_class_prediction() -> None:
    # Predict all "positive"; negative and neutral get f1=0
    gold = ["positive", "neutral", "negative"]
    pred = ["positive", "positive", "positive"]
    # positive f1=0.5, neutral f1=0, negative f1=0 → macro = 0.5/3
    assert macro_f1(gold, pred, LABELS_3) == pytest.approx(0.5 / 3, rel=1e-4)


def test_macro_f1_known_case() -> None:
    gold = ["positive", "positive", "negative", "negative", "neutral"]
    pred = ["positive", "negative", "negative", "positive", "neutral"]
    # from test_per_class_metrics_known_case: f1s = 0.5, 0.5, 1.0
    expected = (0.5 + 0.5 + 1.0) / 3
    assert macro_f1(gold, pred, LABELS_3) == pytest.approx(expected, rel=1e-4)


# ---------------------------------------------------------------------------
# confusion_matrix
# ---------------------------------------------------------------------------


def test_confusion_matrix_perfect() -> None:
    labels = ["positive", "neutral", "negative"]
    gold = ["positive", "neutral", "negative"]
    cm = confusion_matrix(gold, gold, labels)
    assert cm == ((1, 0, 0), (0, 1, 0), (0, 0, 1))


def test_confusion_matrix_all_predicted_first() -> None:
    labels = ["positive", "neutral", "negative"]
    gold = ["positive", "neutral", "negative"]
    pred = ["positive", "positive", "positive"]
    cm = confusion_matrix(gold, pred, labels)
    # true positive (row 0): all predicted positive → (1, 0, 0)
    assert cm[0] == (1, 0, 0)
    # true neutral (row 1): predicted positive → (1, 0, 0)
    assert cm[1] == (1, 0, 0)
    # true negative (row 2): predicted positive → (1, 0, 0)
    assert cm[2] == (1, 0, 0)


def test_confusion_matrix_oov_pred_ignored() -> None:
    # Predictions containing out-of-vocabulary labels are silently ignored
    labels = ["positive", "neutral"]
    gold = ["positive", "neutral"]
    pred = ["positive", "unknown"]  # "unknown" is OOV
    cm = confusion_matrix(gold, pred, labels)
    # Only the first pair is counted; second pred is ignored
    assert cm[0][0] == 1  # true positive, pred positive
    assert cm[1][0] == 0  # true neutral not counted (OOV pred ignored)
    assert cm[1][1] == 0
