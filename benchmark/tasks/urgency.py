from __future__ import annotations

from benchmark.tasks._metrics import accuracy, confusion_matrix, macro_f1, per_class_metrics
from benchmark.tasks.schema import TaskScore

TASK_ID = "URG"
LABELS: tuple[str, ...] = ("low", "medium", "high")
PRIMARY_METRIC = "macro_f1"
DESCRIPTION = (
    "3-class urgency detection for e-commerce reviews. "
    "Labels: high (explicit refund/return demand, safety/health risk, or immediate seller "
    "escalation required) / medium (quality defect or significant complaint without explicit "
    "escalation) / low (positive review, mild feedback, or general praise with minor issues). "
    "Gold label assigned by human annotator applying the written rubric above, independently "
    "of any system under test."
)


def score(gold: list[str], pred: list[str]) -> TaskScore:
    """Score predicted urgency labels against gold labels.

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
