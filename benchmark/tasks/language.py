from __future__ import annotations

from benchmark.tasks._metrics import accuracy, confusion_matrix, macro_f1, per_class_metrics
from benchmark.tasks.schema import TaskScore

TASK_ID = "LANG"
LABELS: tuple[str, ...] = ("en", "hi", "hi-en")
PRIMARY_METRIC = "accuracy"
DESCRIPTION = (
    "3-class language / code-mix identification for Indian e-commerce reviews. "
    "Labels: en (English only) / hi (Hindi in Devanagari script) / hi-en (Hinglish — "
    "Latin-script code-mix of Hindi and English). "
    "Gold label assigned via script-fraction heuristic and human validation."
)


def score(gold: list[str], pred: list[str]) -> TaskScore:
    """Score predicted language-ID labels against gold labels.

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
