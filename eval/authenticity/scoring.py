"""Pure scoring helpers for the authenticity eval: strict P/R/F1, bootstrap CIs, claim gating.

No LLM, no network, no file I/O -- everything here is deterministic given its inputs so it can be
unit-tested on tiny synthetic sets.

Why this exists next to runner.compute_metrics: that legacy helper maps an undefined precision
(no positive predictions) to 0.0, which reads as "the model was wrong". Here an undefined
metric is `None`, never a fabricated number.
"""

from __future__ import annotations

import random
from typing import Any

from eval.bootstrap import DEFAULT_N_RESAMPLES, DEFAULT_SEED, bootstrap_ci

# Below this many actual positives, a recall/precision estimate is not a claim (bootstrap coverage
# degrades badly under ~30 -- see eval/bootstrap.py's LIMITATION section).
MIN_POSITIVES_FOR_CLAIM = 30


def confusion(y_true: list[bool], y_pred: list[bool]) -> dict[str, int]:
    """Confusion-matrix counts for the flagged (positive) class."""
    if len(y_true) != len(y_pred):
        raise ValueError(f"length mismatch: {len(y_true)} labels vs {len(y_pred)} predictions")
    tp = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t and p)
    fp = sum(1 for t, p in zip(y_true, y_pred, strict=True) if not t and p)
    fn = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t and not p)
    tn = sum(1 for t, p in zip(y_true, y_pred, strict=True) if not t and not p)
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


def _ratio(num: int, den: int) -> float | None:
    return num / den if den > 0 else None


def strict_metrics(tp: int, fp: int, fn: int) -> dict[str, float | None]:
    """Precision / recall / F1 where an undefined value is None (not 0.0).

    precision undefined when nothing was predicted positive; recall undefined when there are no
    actual positives; F1 = 2tp / (2tp + fp + fn), undefined when that denominator is 0.
    """
    return {
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "f1": _ratio(2 * tp, 2 * tp + fp + fn),
    }


def _bootstrap_f1_ci(
    y_true: list[bool], y_pred: list[bool], n_resamples: int, seed: int
) -> tuple[float, float] | None:
    """Paired percentile bootstrap for F1 (resample whole items). None if F1 never defined."""
    n = len(y_true)
    rng = random.Random(seed)
    vals: list[float] = []
    for _ in range(n_resamples):
        tp = fp = fn = 0
        for _ in range(n):
            i = rng.randrange(n)
            t, p = y_true[i], y_pred[i]
            tp += t and p
            fp += (not t) and p
            fn += t and (not p)
        den = 2 * tp + fp + fn
        if den > 0:
            vals.append(2 * tp / den)
    if not vals:
        return None
    vals.sort()
    lo = vals[int(0.025 * len(vals))]
    hi = vals[min(int(0.975 * len(vals)), len(vals) - 1)]
    return (lo, hi)


def _ci_dict(ci: tuple[float, float] | None) -> dict[str, float] | None:
    return None if ci is None else {"lower": ci[0], "upper": ci[1]}


def labelled_report(
    y_true: list[bool],
    y_pred: list[bool],
    *,
    is_held_out: bool,
    n_resamples: int = DEFAULT_N_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """P/R/F1 + bootstrap 95% CIs + base rate + claim gating, against a label source that exists.

    Precision's CI resamples the truth values of the flagged items and recall's resamples the
    predictions on the actual positives, both via eval.bootstrap.bootstrap_ci (seed 42); F1 uses a
    paired item-level bootstrap with the same seed. An undefined metric gets `value: None` and
    `ci_95: None`.

    `claim_supported` is False unless n_pos >= MIN_POSITIVES_FOR_CLAIM AND the label set is a
    genuine held-out sample (an in-sample fixture set cannot support a generalisation claim).
    """
    cm = confusion(y_true, y_pred)
    m = strict_metrics(cm["tp"], cm["fp"], cm["fn"])
    n = len(y_true)
    n_pos = cm["tp"] + cm["fn"]
    n_pred_pos = cm["tp"] + cm["fp"]

    flagged_truth = [1.0 if t else 0.0 for t, p in zip(y_true, y_pred, strict=True) if p]
    positive_pred = [1.0 if p else 0.0 for t, p in zip(y_true, y_pred, strict=True) if t]
    precision_ci = bootstrap_ci(flagged_truth, n_resamples, seed) if flagged_truth else None
    recall_ci = bootstrap_ci(positive_pred, n_resamples, seed) if positive_pred else None
    f1_ci = _bootstrap_f1_ci(y_true, y_pred, n_resamples, seed) if n else None

    reasons: list[str] = []
    if n_pos < MIN_POSITIVES_FOR_CLAIM:
        reasons.append(
            f"n_pos={n_pos} < {MIN_POSITIVES_FOR_CLAIM}: too few positives for a "
            "precision/recall claim"
        )
    if not is_held_out:
        reasons.append("label set is an in-sample fixture set, not a held-out sample")

    return {
        "n": n,
        "n_pos": n_pos,
        "n_pred_pos": n_pred_pos,
        "base_rate_positive": _ratio(n_pos, n),
        "confusion_matrix": cm,
        "precision": {"value": m["precision"], "n": n_pred_pos, "ci_95": _ci_dict(precision_ci)},
        "recall": {"value": m["recall"], "n": n_pos, "ci_95": _ci_dict(recall_ci)},
        "f1": {"value": m["f1"], "n": n, "ci_95": _ci_dict(f1_ci)},
        "bootstrap": {"n_resamples": n_resamples, "seed": seed},
        "claim_supported": not reasons,
        "claim_reason": "; ".join(reasons) if reasons else None,
    }


def predictions_only_report(y_pred: list[bool]) -> dict[str, Any]:
    """Flag rate when NO ground-truth label exists. Flag rate is NOT precision."""
    n = len(y_pred)
    n_flagged = sum(y_pred)
    return {
        "n": n,
        "n_flagged": n_flagged,
        "flag_rate": _ratio(n_flagged, n),
        "precision": None,
        "recall": None,
        "f1": None,
        "claim_supported": False,
        "claim_reason": (
            "no ground-truth authenticity label exists for this corpus; the flag rate is the "
            "share of items the system flagged, not a precision or recall"
        ),
    }
