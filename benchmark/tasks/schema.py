from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClassMetrics:
    """Per-class precision, recall, F1, and support count."""

    precision: float
    recall: float
    f1: float
    support: int


@dataclass
class TaskScore:
    """Scoring result for a single task run (one system, one task, N samples)."""

    task_id: str
    labels: tuple[str, ...]
    n_samples: int
    accuracy: float
    macro_f1: float
    per_class: dict[str, ClassMetrics]
    # confusion[true_label_index][predicted_label_index] — matches `labels` ordering
    confusion: tuple[tuple[int, ...], ...]

    def primary_metric(self, metric: str) -> float:
        if metric == "accuracy":
            return self.accuracy
        if metric == "macro_f1":
            return self.macro_f1
        raise ValueError(f"Unknown metric: {metric!r}")
