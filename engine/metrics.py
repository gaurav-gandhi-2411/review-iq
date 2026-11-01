"""Metrics: macro-F1 headline, accuracy, per-class F1, confusion, bootstrap CI, open-set AUROC."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from sklearn.metrics import confusion_matrix, f1_score, roc_auc_score


def macro_f1(y: np.ndarray, p: np.ndarray, labels: list[int] | None = None) -> float:
    """Macro-F1 over classes that have at least one true item in `y`.

    A class with no support cannot be scored, and counting it as F1 = 0 deflates the point estimate
    while a bootstrap resample may or may not contain it, so the point estimate and its CI would
    disagree (seen on MASSIVE, S21). False positives into a supported class still lower that class's
    precision, so predicting a phantom class is not free.
    """
    present = np.unique(y)
    use = present if labels is None else np.array([lab for lab in labels if lab in set(present)])
    return float(f1_score(y, p, labels=use, average="macro", zero_division=0))


def per_class_f1(y: np.ndarray, p: np.ndarray, labels: list[int]) -> np.ndarray:
    return f1_score(y, p, labels=labels, average=None, zero_division=0)


def accuracy(y: np.ndarray, p: np.ndarray) -> float:
    return float((y == p).mean())


def top_confusions(y: np.ndarray, p: np.ndarray, names: list[str], k: int = 10) -> list[dict]:
    cm = confusion_matrix(y, p, labels=list(range(len(names))))
    np.fill_diagonal(cm, 0)
    order = np.argsort(-cm, axis=None)[:k]
    out = []
    for flat in order:
        i, j = np.unravel_index(flat, cm.shape)
        if cm[i, j] > 0:
            out.append({"true": names[i], "pred": names[j], "n": int(cm[i, j])})
    return out


def bootstrap_ci(
    y: np.ndarray,
    p: np.ndarray,
    fn: Callable[[np.ndarray, np.ndarray], float],
    n_boot: int = 1000,
    seed: int = 42,
) -> tuple[float, float]:
    """95% percentile CI over resampled items (spec: 1,000 resamples, seed 42)."""
    rng = np.random.default_rng(seed)
    n = len(y)
    vals = []
    for _ in range(n_boot):
        i = rng.integers(0, n, n)
        vals.append(fn(y[i], p[i]))
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return float(lo), float(hi)


def known_vs_unknown(score_known: np.ndarray, score_unknown: np.ndarray) -> dict[str, float]:
    """Score convention: higher = more 'known'. AUROC and FPR (unknown accepted) at 95% known TPR."""
    y = np.r_[np.ones(len(score_known)), np.zeros(len(score_unknown))]
    s = np.r_[score_known, score_unknown]
    thr95 = np.percentile(score_known, 5)
    return {
        "auroc": float(roc_auc_score(y, s)),
        "fpr_at_95_tpr": float((score_unknown >= thr95).mean()),
    }


def bootstrap_open_set(
    score_known: np.ndarray,
    score_unknown: np.ndarray,
    thr: float,
    n_boot: int = 1000,
    seed: int = 42,
) -> dict[str, list[float]]:
    """95% CIs for AUROC, rejection recall and known retention at a FIXED threshold.

    Known and unknown items are resampled independently (they are separate populations); the
    threshold stays fixed because it was chosen on validation, not on these items.
    """
    rng = np.random.default_rng(seed)
    nk, nu = len(score_known), len(score_unknown)
    aur, rej, ret = [], [], []
    for _ in range(n_boot):
        k = score_known[rng.integers(0, nk, nk)]
        u = score_unknown[rng.integers(0, nu, nu)]
        y = np.r_[np.ones(nk), np.zeros(nu)]
        aur.append(roc_auc_score(y, np.r_[k, u]))
        rej.append(float((u < thr).mean()))
        ret.append(float((k >= thr).mean()))
    q = lambda v: [round(float(x), 4) for x in np.percentile(v, [2.5, 97.5])]  # noqa: E731
    return {"auroc_ci95": q(aur), "rejection_recall_ci95": q(rej), "retention_ci95": q(ret)}
