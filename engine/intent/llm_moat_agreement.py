"""Groq-vs-local agreement for the gpt-oss-20b moat arm (spec Amendment 7). Aggregates only; no review text is read.

Per task, on the ids present in BOTH row files: label agreement, Cohen's kappa and Spearman correlation of the verbalised
confidences, each with an item-bootstrap 95 percent CI (1,000 resamples, seed 42). Pairs where either side is unparseable are
excluded from the three statistics and counted separately. Accuracy against the sealed consensus is reported for both arms on
the same ids so the infrastructure difference can be read in the metric the comparison actually uses.

    python -m engine.intent.llm_moat_agreement --groq D:/ml-cache/s23/llm/openai_gpt-oss-20b.jsonl \
        --local D:/ml-cache/s23/llm_local/openai_gpt-oss-20b__local.jsonl --out reports/intent/moat_local20b/agreement.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from engine.intent import llm_moat as M

B = 1000


def _read(path: Path) -> dict[str, dict]:
    return {
        r["id"]: r for r in (json.loads(ln) for ln in path.open(encoding="utf-8") if ln.strip())
    }


def _label_conf(row: dict, task: str) -> tuple[str | None, float]:
    a = (row["ans"] or {}).get(task) or {}
    lab = str(a.get("label", "")).strip().lower()
    try:
        c = float(a.get("confidence", 0)) / 100
    except (TypeError, ValueError):
        c = 0.0
    return (lab if lab in M.CLASSES[task] else None), c


def kappa(a: np.ndarray, b: np.ndarray) -> float:
    po = float(np.mean(a == b))
    pe = sum(
        float(np.mean(a == c)) * float(np.mean(b == c)) for c in np.unique(np.concatenate([a, b]))
    )
    return float("nan") if pe >= 1 else (po - pe) / (1 - pe)


def rankdata(x: np.ndarray) -> np.ndarray:
    """Average ranks (ties share the mean rank), as scipy.stats.rankdata; scipy is not a dependency here."""
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x), dtype=float)
    xs = x[order]
    start = 0
    for end in range(1, len(x) + 1):
        if end == len(x) or xs[end] != xs[start]:
            ranks[order[start:end]] = (start + end - 1) / 2 + 1
            start = end
    return ranks


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    rx, ry = rankdata(x), rankdata(y)
    if rx.std() == 0 or ry.std() == 0:
        return float("nan")
    return float(np.corrcoef(rx, ry)[0, 1])


def _ci(stat, idx_arrays: list[np.ndarray], seed: int = 42) -> list[float | None]:
    rng = np.random.default_rng(seed)
    n = len(idx_arrays[0])
    vals = []
    for _ in range(B):
        s = rng.integers(0, n, n)
        vals.append(stat(*[a[s] for a in idx_arrays]))
    v = np.array(vals, dtype=float)
    v = v[~np.isnan(v)]
    if len(v) < B // 2:
        return [None, None]
    return [round(float(np.percentile(v, 2.5)), 4), round(float(np.percentile(v, 97.5)), 4)]


def _r(x: float) -> float | None:
    return None if np.isnan(x) else round(float(x), 4)


def analyse(groq: dict[str, dict], local: dict[str, dict], gold_test: dict) -> dict:
    ids = sorted(set(groq) & set(local))
    res: dict = {"n_groq": len(groq), "n_local": len(local), "n_overlap": len(ids), "tasks": {}}
    for task in M.TASKS:
        g = [_label_conf(groq[i], task) for i in ids]
        lo = [_label_conf(local[i], task) for i in ids]
        ok = [k for k in range(len(ids)) if g[k][0] is not None and lo[k][0] is not None]
        ga = np.array([g[k][0] for k in ok])
        la = np.array([lo[k][0] for k in ok])
        gc = np.array([g[k][1] for k in ok])
        lc = np.array([lo[k][1] for k in ok])
        agree = lambda a, b: float(np.mean(a == b))  # noqa: E731
        gold = np.array([gold_test[task].get(ids[k], "") for k in ok])
        res["tasks"][task] = {
            "n_pairs_both_parsed": len(ok),
            "n_unparseable_groq": sum(x[0] is None for x in g),
            "n_unparseable_local": sum(x[0] is None for x in lo),
            "label_agreement": _r(agree(ga, la)),
            "label_agreement_ci95": _ci(agree, [ga, la]),
            "cohen_kappa": _r(kappa(ga, la)),
            "cohen_kappa_ci95": _ci(kappa, [ga, la]),
            "spearman_confidence": _r(spearman(gc, lc)),
            "spearman_confidence_ci95": _ci(spearman, [gc, lc]),
            "accuracy_vs_consensus_groq": _r(float(np.mean(ga == gold))),
            "accuracy_vs_consensus_local": _r(float(np.mean(la == gold))),
            "mean_confidence_groq": _r(float(gc.mean())),
            "mean_confidence_local": _r(float(lc.mean())),
        }
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--groq", type=Path, required=True)
    ap.add_argument("--local", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    labels = json.loads((M.ROOT / "reports/intent/labels/labels.json").read_text(encoding="utf-8"))
    res = analyse(_read(a.groq), _read(a.local), labels["labels"]["test"])
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({t: v["label_agreement"] for t, v in res["tasks"].items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
