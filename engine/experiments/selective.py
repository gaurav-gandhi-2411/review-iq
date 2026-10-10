"""Selective prediction: precision at coverage, and the paired fine-tuned vs LLM comparison.

PRECISION AT COVERAGE is the product claim (GG, S21): rank answered items by confidence, answer the
top fraction ("coverage"), send the rest to a human, and report the precision (accuracy) of what was
answered. Two summaries per arm, each with a 95% bootstrap CI (1,000 resamples, seed 42):

  - coverage_at_p95: the largest coverage whose precision is still >= 0.95 (0 if none is);
  - precision at fixed coverage 0.9 / 0.8 / 0.7.

Ties in confidence (an LLM's verbalised 0-100 score ties a lot) are never split arbitrarily: an
operating point is a confidence THRESHOLD, so the achieved coverage jumps over a tie group, and the
achieved coverage is reported next to the number.

    python -m engine.experiments.selective curves RESULTS_DIR OUT.json
    python -m engine.experiments.selective paired LLM.json FINETUNED.npz OUT.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

COVERAGES = (0.9, 0.8, 0.7)
TARGET_PRECISION = 0.95
SEED = 42


def operating_points(correct: np.ndarray, conf: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(coverage, precision) at every distinct confidence threshold, highest confidence first."""
    order = np.argsort(-conf, kind="stable")
    c, s = correct[order].astype(float), conf[order]
    cum = np.cumsum(c)
    n = np.arange(1, len(c) + 1)
    last_of_group = np.r_[s[1:] != s[:-1], True]  # a threshold admits the whole tie group
    return n[last_of_group] / len(c), cum[last_of_group] / n[last_of_group]


def coverage_at_precision(correct: np.ndarray, conf: np.ndarray, target: float) -> float:
    cov, prec = operating_points(correct, conf)
    ok = prec >= target
    return float(cov[ok].max()) if ok.any() else 0.0


def precision_at_coverage(
    correct: np.ndarray, conf: np.ndarray, coverage: float
) -> tuple[float, float]:
    """(precision, achieved coverage) at the smallest threshold whose coverage is >= `coverage`."""
    cov, prec = operating_points(correct, conf)
    i = int(np.argmax(cov >= coverage - 1e-12))
    return float(prec[i]), float(cov[i])


def summarize(correct: np.ndarray, conf: np.ndarray, n_boot: int = 1000) -> dict:
    rng = np.random.default_rng(SEED)
    n = len(correct)
    boots: dict[str, list[float]] = {"cov95": []}
    for cv in COVERAGES:
        boots[f"p{cv}"] = []
    for _ in range(n_boot):
        i = rng.integers(0, n, n)
        boots["cov95"].append(coverage_at_precision(correct[i], conf[i], TARGET_PRECISION))
        for cv in COVERAGES:
            boots[f"p{cv}"].append(precision_at_coverage(correct[i], conf[i], cv)[0])

    def ci(key: str) -> list[float]:
        return [round(float(x), 4) for x in np.percentile(boots[key], [2.5, 97.5])]

    out = {
        "n": int(n),
        "accuracy": round(float(correct.mean()), 4),
        "coverage_at_precision_0.95": round(
            coverage_at_precision(correct, conf, TARGET_PRECISION), 4
        ),
        "coverage_at_precision_0.95_ci95": ci("cov95"),
    }
    for cv in COVERAGES:
        p, achieved = precision_at_coverage(correct, conf, cv)
        out[f"precision_at_coverage_{cv}"] = {
            "precision": round(p, 4), "achieved_coverage": round(achieved, 4), "ci95": ci(f"p{cv}")
        }  # fmt: skip
    return out


def curves(results_dir: Path) -> dict:
    """Fine-tuned arms: every run_*.npz written by engine.experiments.run, per confidence score."""
    out: dict[str, dict] = {}
    for f in sorted(results_dir.glob("*.npz")):
        z = np.load(f)
        correct = z["pred"] == z["y"]
        out[f.stem] = {
            name: summarize(correct, z[name])
            for name in ("msp", "energy", "maha")
            if name in z.files
        }
    return out


def paired(llm_json: Path, ft_npz: Path) -> dict:
    """Same items, both arms. Fine-tuned side is looked up by test index; LLM side by its records."""
    d = json.loads(llm_json.read_text(encoding="utf-8"))
    z = np.load(ft_npz)
    pos = {int(i): k for k, i in enumerate(z["test_idx"])}
    recs = [r for r in d["records"] if r["test_idx"] in pos]
    ft_correct = np.array(
        [z["pred"][pos[r["test_idx"]]] == z["y"][pos[r["test_idx"]]] for r in recs]
    )
    ft_conf = np.array([z["msp"][pos[r["test_idx"]]] for r in recs])
    llm_correct = np.array([r["correct"] for r in recs])
    llm_conf = np.array(
        [-1 if r["confidence"] is None else r["confidence"] for r in recs], dtype=float
    )
    rng = np.random.default_rng(SEED)
    n = len(recs)
    diffs = []
    for _ in range(1000):
        i = rng.integers(0, n, n)
        diffs.append(ft_correct[i].mean() - llm_correct[i].mean())
    b = int((ft_correct & ~llm_correct).sum())  # fine-tuned right, LLM wrong
    c = int((~ft_correct & llm_correct).sum())
    from scipy.stats import binomtest

    p = float(binomtest(b, b + c, 0.5).pvalue) if b + c else 1.0
    return {
        "n_paired": n,
        "finetuned_accuracy": round(float(ft_correct.mean()), 4),
        "llm_accuracy": round(float(llm_correct.mean()), 4),
        "accuracy_difference": round(float(ft_correct.mean() - llm_correct.mean()), 4),
        "accuracy_difference_ci95": [round(float(x), 4) for x in np.percentile(diffs, [2.5, 97.5])],
        "mcnemar_exact_p": round(p, 5),
        "finetuned_only_correct": b,
        "llm_only_correct": c,
        "finetuned_selective": summarize(ft_correct, ft_conf),
        "llm_selective_verbalised_confidence": summarize(llm_correct, llm_conf),
    }


def main() -> None:
    mode = sys.argv[1]
    if mode == "curves":
        res = curves(Path(sys.argv[2]))
        Path(sys.argv[3]).write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    elif mode == "paired":
        res = paired(Path(sys.argv[2]), Path(sys.argv[3]))
        Path(sys.argv[4]).write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    else:
        raise SystemExit(__doc__)
    print(f"wrote {sys.argv[-1]}")


if __name__ == "__main__":
    main()
