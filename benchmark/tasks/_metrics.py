from __future__ import annotations

from benchmark.tasks.schema import ClassMetrics


def _validate(gold: list[str], pred: list[str]) -> None:
    if len(gold) != len(pred):
        raise ValueError(f"Length mismatch: gold={len(gold)}, pred={len(pred)}")
    if not gold:
        raise ValueError("Empty inputs: gold and pred must be non-empty")


def accuracy(gold: list[str], pred: list[str]) -> float:
    _validate(gold, pred)
    return sum(g == p for g, p in zip(gold, pred, strict=True)) / len(gold)


def per_class_metrics(
    gold: list[str], pred: list[str], labels: list[str]
) -> dict[str, ClassMetrics]:
    """Compute precision, recall, F1, and support for each label independently."""
    _validate(gold, pred)
    out: dict[str, ClassMetrics] = {}
    for lbl in labels:
        tp = sum(g == lbl and p == lbl for g, p in zip(gold, pred, strict=True))
        fp = sum(g != lbl and p == lbl for g, p in zip(gold, pred, strict=True))
        fn = sum(g == lbl and p != lbl for g, p in zip(gold, pred, strict=True))
        support = sum(g == lbl for g in gold)
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2.0 * prec * rec / (prec + rec) if (prec + rec) > 0.0 else 0.0
        out[lbl] = ClassMetrics(precision=prec, recall=rec, f1=f1, support=support)
    return out


def macro_f1(gold: list[str], pred: list[str], labels: list[str]) -> float:
    """Unweighted mean F1 across all labels (including zero-support labels)."""
    pc = per_class_metrics(gold, pred, labels)
    return sum(m.f1 for m in pc.values()) / len(labels)


def confusion_matrix(
    gold: list[str], pred: list[str], labels: list[str]
) -> tuple[tuple[int, ...], ...]:
    """Return confusion[true_idx][pred_idx]; out-of-vocabulary predictions are ignored."""
    _validate(gold, pred)
    idx = {lbl: i for i, lbl in enumerate(labels)}
    n = len(labels)
    mat: list[list[int]] = [[0] * n for _ in range(n)]
    for g, p in zip(gold, pred, strict=True):
        gi = idx.get(g)
        pi = idx.get(p)
        if gi is not None and pi is not None:
            mat[gi][pi] += 1
    return tuple(tuple(row) for row in mat)
