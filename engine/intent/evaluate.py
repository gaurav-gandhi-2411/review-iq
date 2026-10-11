"""Evaluate the review-intent model on the SEALED test set (spec Amendments 2 and 5). Everything is "against panel
consensus" (silver), never against the truth.

    python -m engine.intent.evaluate --labels <dir>/labels.json --manifest reports/labelling/scale/manifest.json \
        --pred-dir <run dir> --out reports/intent/eval_run1.json [--texts scale.jsonl --judges A S B]

Per task (the four core tasks and the beauty/food aspect-sentiment tasks):
- precision at coverage with the CONSERVATIVE rule: the threshold is the lowest confidence on VALIDATION whose Wilson
  lower bound for precision reaches the target; it is then applied once to the sealed set. The best-on-test (ORACLE)
  numbers come from the same curve and are labelled oracle everywhere. Confidence = calibrated max-softmax.
- macro-F1 and per-class F1 (floor 0.60) with 95 percent bootstrap CIs. A class with fewer than 20 sealed positives is
  "insufficient data": it is never given a metric, and a task with fewer than two scored classes gets no macro-F1.
- the consensus ceiling: share of sealed items that have a label for the task (no coverage claim may exceed it).
CIs resample ITEMS (seed 42): the corpora carry no product or reviewer ids (manifest split_rule), so the cluster
bootstrap of Amendment 2 is not possible and the intervals are narrower than a cluster bootstrap would give.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from pathlib import Path

import numpy as np

from engine import metrics as M
from engine.experiments import selective as SEL
from engine.labelling.repilot2_report import wilson
from engine.labelling.scale_report import _nan_to_none

MIN_POSITIVES = 20  # Amendment 5: fewer sealed positives is 'insufficient data'
LOW_SUPPORT = 30  # Amendment 2: headline support; 20-29 are scored but flagged
FLOOR = 0.60  # Amendment 2 per-class F1 floor
TARGETS = (0.95, 0.90)
SEED = 42


def _git_sha() -> str:
    # not engine.experiments.run's: importing that module pulls in torch, which evaluation does not need
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _r(x: float | None, nd: int = 4) -> float | None:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), nd)


def val_threshold(correct: np.ndarray, conf: np.ndarray, target: float) -> float:
    """Conservative rule: the lowest confidence whose Wilson lower bound for precision is >= target (inf if none)."""
    order = np.argsort(-conf, kind="stable")
    c, s = correct[order].astype(float), conf[order]
    n = np.arange(1, len(c) + 1, dtype=float)
    last = np.r_[s[1:] != s[:-1], True]  # a threshold admits a whole tie group
    ok = SEL.wilson_lower(np.cumsum(c)[last], n[last]) >= target
    return float(s[last][ok].min()) if ok.any() else math.inf


def apply_threshold(
    correct: np.ndarray, conf: np.ndarray, thr: float, target: float, n_boot: int
) -> dict:
    """Precision and coverage on the sealed set at a threshold fixed beforehand, with Wilson and bootstrap CIs."""
    keep = conf >= thr
    n, k = len(correct), int(keep.sum())
    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, n, (n_boot, n))
    kb, cb = keep[idx], correct[idx] & keep[idx]
    ans = kb.sum(1)
    prec = np.where(ans > 0, cb.sum(1) / np.maximum(ans, 1), np.nan)
    p = float(correct[keep].mean()) if k else None
    ci = lambda v: [_r(x) for x in np.nanpercentile(v, [2.5, 97.5])] if k else None  # noqa: E731
    return {
        "target": target, "threshold": None if math.isinf(thr) else _r(thr, 6), "n_answered": k,
        "coverage": _r(k / n), "coverage_ci95": ci(ans / n),
        "precision": _r(p), "precision_wilson95": [_r(x) for x in wilson(int(correct[keep].sum()), k)] if k else None,
        "precision_ci95": ci(prec), "reached_target": bool(p is not None and p >= target),
    }  # fmt: skip


def _f1_matrix(y: np.ndarray, p: np.ndarray, k: int) -> np.ndarray:
    """Per-class F1 for arrays shaped (n,) or (B, n) -> (K,) or (B, K); NaN where the class has no true item."""
    cls = np.arange(k)
    yy, pp = y[..., None] == cls, p[..., None] == cls
    tp, fp, fn = (yy & pp).sum(-2), (~yy & pp).sum(-2), (yy & ~pp).sum(-2)
    f1 = np.where(2 * tp + fp + fn > 0, 2 * tp / np.maximum(2 * tp + fp + fn, 1), 0.0)
    return np.where(tp + fn > 0, f1, np.nan)


def class_stats(y: np.ndarray, pred: np.ndarray, classes: list[str], n_boot: int) -> dict:
    """Accuracy, macro-F1 over scored classes, per-class F1 with the floor, all with CIs."""
    k, n = len(classes), len(y)
    support = np.bincount(y, minlength=k)
    scored = [c for c in range(k) if support[c] >= MIN_POSITIVES]
    idx = np.random.default_rng(SEED).integers(0, n, (n_boot, n))
    fb = _f1_matrix(y[idx], pred[idx], k)
    f1 = _f1_matrix(y, pred, k)
    out: dict = {
        "n": n, "accuracy": _r((y == pred).mean()),
        "accuracy_wilson95": [_r(x) for x in wilson(int((y == pred).sum()), n)], "classes": {},
    }  # fmt: skip
    for c, name in enumerate(classes):
        if c not in scored:
            out["classes"][name] = {"n": int(support[c]), "insufficient_data": True}
            continue
        lo, hi = np.nanpercentile(fb[:, c], [2.5, 97.5])
        out["classes"][name] = {
            "n": int(support[c]), "insufficient_data": False, "low_support_lt30": bool(support[c] < LOW_SUPPORT),
            "f1": _r(f1[c]), "f1_ci95": [_r(lo), _r(hi)], "below_floor": bool(f1[c] < FLOOR),
        }  # fmt: skip
    if len(scored) >= 2:
        macro = np.nanmean(fb[:, scored], axis=1)
        out["macro_f1"] = _r(M.macro_f1(y, pred, scored))
        out["macro_f1_ci95"] = [_r(x) for x in np.percentile(macro, [2.5, 97.5])]
        out["macro_f1_classes"] = [classes[c] for c in scored]
    else:
        out["macro_f1"] = "insufficient data (fewer than 2 classes with 20 sealed positives)"
    return out


def evaluate_task(
    classes: list[str], val: tuple[np.ndarray, np.ndarray], test: tuple[np.ndarray, np.ndarray],
    n_boot: int = 1000,
) -> dict:  # fmt: skip
    """val/test = (gold class indices, calibrated probabilities (n, K)) over the LABELLED items of each split."""
    (yv, pv), (yt, pt) = val, test
    if len(yt) < MIN_POSITIVES:
        return {"n_sealed_labelled": len(yt), "insufficient_data": True}
    pred, conf = pt.argmax(1), pt.max(1)
    correct = pred == yt
    out = {"insufficient_data": False, **class_stats(yt, pred, classes, n_boot), "selective": {}}
    for target in TARGETS:
        thr = val_threshold(pv.argmax(1) == yv, pv.max(1), target) if len(yv) else math.inf
        out["selective"][f"conservative_p{target}"] = {
            "rule": "lowest validation confidence whose Wilson lower bound reaches the target",
            "n_validation": len(yv),
            **apply_threshold(correct, conf, thr, target, n_boot),
        }
    oracle = SEL.summarize(correct, conf, n_boot)
    out["selective"]["oracle"] = {
        "label": "ORACLE: best on the sealed set itself, not deployable",
        **oracle,
    }
    return out


def task_arrays(
    labels: dict, pred: dict, task: str
) -> dict[str, tuple[list[str], np.ndarray, np.ndarray]]:
    """split -> (ids, gold class indices, calibrated probs) over the items LABELLED for `task`."""
    classes = pred["classes"]
    out = {}
    for split in ("val", "test"):
        ids = pred["splits"][split]["ids"]
        lab = labels["labels"][split][task]
        sel = [j for j, i in enumerate(ids) if i in lab]
        probs = np.array(pred["splits"][split]["probs"]).reshape(len(ids), len(classes))[sel]
        out[split] = (
            [ids[j] for j in sel],
            np.array([classes.index(lab[ids[j]]) for j in sel], dtype=np.int64),
            probs,
        )
    return out


def slices(ids: list[str], y: np.ndarray, pred: np.ndarray, strata: dict[str, str]) -> dict:
    """Accuracy per stratum (language/category); below 100 consensus items a slice is not claimed (Amendment 2, 8)."""
    out = {}
    for s in sorted({strata[i] for i in ids}):
        m = np.array([strata[i] == s for i in ids])
        n = int(m.sum())
        out[s] = {"n": n, "claimed": n >= 100}
        if n >= 100:
            out[s]["accuracy"] = _r((y[m] == pred[m]).mean())
    return out


def evaluate_all(
    labels: dict, preds: dict[str, dict], strata: dict[str, str], n_boot: int = 1000
) -> dict:
    """Per-task results for every task in labels['classes']; a task with no prediction file is flagged."""
    tasks: dict = {}
    for task in labels["classes"]:
        if task not in preds:
            tasks[task] = {"missing_predictions": True}
            continue
        arr = task_arrays(labels, preds[task], task)
        (_, yv, pv), (ids_t, yt, pt) = arr["val"], arr["test"]
        res = evaluate_task(list(preds[task]["classes"]), (yv, pv), (yt, pt), n_boot)
        n_sealed = len(preds[task]["splits"]["test"]["ids"])
        res["consensus_ceiling"] = {
            "n_labelled": len(yt), "n_sealed": n_sealed, "fraction": _r(len(yt) / n_sealed),
            "wilson95": [_r(x) for x in wilson(len(yt), n_sealed)],
            "note": "aspect tasks: relative to all sealed items, not only those that mention the aspect"
            if task.startswith("aspect:") else "share of sealed items with a four-judge consensus label",
        }  # fmt: skip
        if not res["insufficient_data"]:
            res["slices_accuracy"] = slices(ids_t, yt, pt.argmax(1), strata)
        tasks[task] = res
    return tasks


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--pred-dir", required=True, help="train output: pred_*.json and metadata.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--n-boot", type=int, default=1000)
    ap.add_argument("--texts", help="private scale.jsonl: enables the TF-IDF baseline")
    ap.add_argument(
        "--judges", nargs="+", help="judge dirs (A, S, B): enables the leave-one-out judges"
    )
    a = ap.parse_args()
    lp, mp, pd = Path(a.labels), Path(a.manifest), Path(a.pred_dir)
    labels = json.loads(lp.read_text(encoding="utf-8"))
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    preds = {}
    for f in sorted(pd.glob("pred_*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        preds[d["task"]] = d
    strata = {r["id"]: r["stratum"] for r in manifest["items"]}
    rep = {
        "provenance": {
            "evaluation_commit": _git_sha(),
            "training_metadata": json.loads((pd / "metadata.json").read_text(encoding="utf-8")),
            "artifacts_sha256_16": {p.name: _sha256(p) for p in [lp, mp, *sorted(pd.glob("pred_*.json"))]},
            "label_basis": "silver: sealed labels are four-judge consensus (Amendment 5), not human truth",
        },
        "n_boot": a.n_boot, "seed": SEED, "bootstrap_unit": "items (no product ids in the corpora)",
        "tasks": evaluate_all(labels, preds, strata, a.n_boot),
    }  # fmt: skip
    if a.texts or a.judges:
        from engine.intent.baselines import attach_baselines  # lazy: baselines imports this module
        from engine.intent.labels import load_texts
        from engine.labelling.scale_report import load_items

        jd = [Path(d) for d in a.judges or []]
        rep["provenance"]["baseline_inputs_sha256_16"] = {
            f"{d.name}/{f.name}": _sha256(f) for d in jd for f in sorted(d.glob("*.jsonl"))
        } | ({Path(a.texts).name: _sha256(Path(a.texts))} if a.texts else {})
        attach_baselines(
            rep["tasks"], labels, preds, load_texts(Path(a.texts)) if a.texts else None,
            load_items(jd) if jd else None, strata, a.n_boot,
        )  # fmt: skip
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(
        json.dumps(_nan_to_none(rep), indent=1, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
