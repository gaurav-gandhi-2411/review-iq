from __future__ import annotations

from benchmark.tasks._metrics import accuracy, confusion_matrix, macro_f1, per_class_metrics
from benchmark.tasks.schema import TaskScore

TASK_ID = "SENT"
LABELS: tuple[str, ...] = ("positive", "neutral", "negative")
PRIMARY_METRIC = "macro_f1"
DESCRIPTION = (
    "3-class sentiment polarity on a single review text. "
    "Labels: positive / neutral / negative. "
    "Gold label derived from star rating (1–2★ → negative, 3★ → neutral, 4–5★ → positive) "
    "with human override for rating-text conflicts."
)


def score(gold: list[str], pred: list[str]) -> TaskScore:
    """Score predicted sentiment labels against gold labels.

    Both lists must contain only values in LABELS; lengths must match.
    """
    labels = list(LABELS)
    return TaskScore(
        task_id=TASK_ID,
        labels=LABELS,
        n_samples=len(gold),
        accuracy=accuracy(gold, pred),
        macro_f1=macro_f1(gold, pred, labels),
        per_class=per_class_metrics(gold, pred, labels),
        confusion=confusion_matrix(gold, pred, labels),
    )
