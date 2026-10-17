"""Decisioning for the review-intent model: per-class thresholds for RARE classes are the DEFAULT (spec Amendment 8).

Why: argmax on a rare class is a brittle operating point. After int8 export the `urgency=high` argmax recall fell from 0.877 (fp32)
to 0.035-0.154 while a threshold on p(high) kept 0.65-0.75 (reports/intent/model_report.md, run 3), and between two same-recipe
runs the argmax recall moved from 0.119 to 0.319. A threshold fitted on VALIDATION for the variant being served absorbs a shift in
the probability scale; argmax cannot.

    thresholds = fit_thresholds(y_val, p_val, classes, rare_for(task))   # validation data only
    pred = decide(p, classes, thresholds)                                  # default mode "threshold"
    pred = decide(p, classes, thresholds, mode="argmax")                   # explicit opt-in, ablations only

Rule: a rare class is predicted when p(class) >= its threshold (the highest-p flagged rare class wins); otherwise the prediction is the
argmax over the NON-rare classes. A rare class with fewer than MIN_VAL_POSITIVES validation positives has no usable threshold: it
stays an ordinary class decided by argmax and `fit_thresholds` reports it as a fallback (never silently).
"""

from __future__ import annotations

import numpy as np

from engine.intent.safety_eval import f1_threshold

MODES = ("threshold", "argmax")
DEFAULT_MODE = "threshold"
MIN_VAL_POSITIVES = 5  # fewer validation positives cannot support a threshold (Amendment 8)
RARE_CLASSES: dict[str, tuple[str, ...]] = {"urgency": ("high",)}  # Amendment 8: urgency=high only


def rare_for(task: str) -> tuple[str, ...]:
    return RARE_CLASSES.get(task, ())


def fit_thresholds(
    y_val: np.ndarray,
    p_val: np.ndarray,
    classes: list[str] | tuple[str, ...],
    rare: tuple[str, ...],
) -> dict[str, dict]:
    """Per rare class: {"threshold": float | None, "n_val_positives": int, "fallback_argmax": bool}.

    Uses VALIDATION labels and probabilities only (y_val = class indices, p_val = (n, K)). The threshold maximises F1 of the class
    (`safety_eval.f1_threshold`, ties to the higher threshold)."""
    out: dict[str, dict] = {}
    for name in rare:
        c = list(classes).index(name)
        pos = y_val == c
        n_pos = int(pos.sum())
        ok = n_pos >= MIN_VAL_POSITIVES
        out[name] = {
            "threshold": round(f1_threshold(p_val[:, c], pos), 6) if ok else None,
            "n_val_positives": n_pos, "fallback_argmax": not ok,
        }  # fmt: skip
    return out


def decide(
    p: np.ndarray, classes: list[str] | tuple[str, ...], thresholds: dict[str, dict] | None,
    mode: str = DEFAULT_MODE,
) -> np.ndarray:  # fmt: skip
    """Class index per row of `p` (n, K). `mode="argmax"` ignores the thresholds (ablation only)."""
    if mode not in MODES:
        raise ValueError(f"unknown decision mode {mode!r}; expected one of {MODES}")
    pred = p.argmax(1)
    if mode == "argmax" or not thresholds:
        return pred
    cls = list(classes)
    active = {
        cls.index(n): t["threshold"] for n, t in thresholds.items() if t["threshold"] is not None
    }
    if not active:
        return pred
    rare_idx = sorted(active)
    other = [c for c in range(len(cls)) if c not in active]
    pred = (
        np.array(other)[p[:, other].argmax(1)] if other else pred
    )  # argmax among non-rare classes
    flagged = np.stack([p[:, c] >= active[c] for c in rare_idx], axis=1)
    score = np.where(flagged, p[:, rare_idx], -1.0)
    hit = flagged.any(1)
    pred[hit] = np.array(rare_idx)[score[hit].argmax(1)]
    return pred
