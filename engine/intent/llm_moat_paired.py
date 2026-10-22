"""Paired comparison of two LLM arms on the same sealed ids (spec Amendment 10: few-shot local vs zero-shot local).

Same per-item correctness/confidence arrays as `llm_moat.llm_arrays`; the bootstrap resamples items ONCE per replicate and applies
the same resample to both arms, so the interval is for the paired difference (not two independent intervals). Seed 42.

    python -m engine.intent.llm_moat_paired --a <fewshot.jsonl> --b <zeroshot.jsonl> --out paired.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from engine.experiments import selective as SEL
from engine.intent import llm_moat as M

TARGET = 0.95


def _stats(c: np.ndarray, k: np.ndarray) -> tuple[float, float]:
    return float(c.mean()), SEL.coverage_at_precision(c, k, TARGET)


def paired_task(
    rows_a: list[dict], rows_b: list[dict], gold: dict[str, str], task: str, n_boot: int = 1000
) -> dict:
    """A minus B on the ids present in both arms: accuracy and coverage at precision 0.95, with item-bootstrap 95% intervals."""
    ids_b = {r["id"] for r in rows_b}
    ids = {r["id"] for r in rows_a} & ids_b
    ra = [r for r in rows_a if r["id"] in ids]
    rb = [r for r in rows_b if r["id"] in ids]
    ca, ka = _aligned(ra, gold, task)
    cb, kb = _aligned(rb, gold, task)
    n = len(ca)
    aa, cova = _stats(ca, ka)
    ab, covb = _stats(cb, kb)
    rng = np.random.default_rng(42)
    d_acc, d_cov = [], []
    for _ in range(n_boot):
        ix = rng.integers(0, n, n)
        x, y = _stats(ca[ix], ka[ix]), _stats(cb[ix], kb[ix])
        d_acc.append(x[0] - y[0])
        d_cov.append(x[1] - y[1])
    ci = lambda v: [round(float(p), 4) for p in np.percentile(v, [2.5, 97.5])]  # noqa: E731
    return {
        "n_matched": n,
        "accuracy": {"a": round(aa, 4), "b": round(ab, 4), "diff": round(aa - ab, 4), "diff_ci95": ci(d_acc)},
        "coverage_at_precision_0.95": {
            "a": round(cova, 4), "b": round(covb, 4), "diff": round(cova - covb, 4), "diff_ci95": ci(d_cov),
        },
    }  # fmt: skip


def _aligned(rows: list[dict], gold: dict[str, str], task: str) -> tuple[np.ndarray, np.ndarray]:
    """`llm_arrays` on rows sorted by id, so two arms line up item for item."""
    return M.llm_arrays(sorted(rows, key=lambda r: r["id"]), gold, task)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", type=Path, required=True, help="arm A rows (e.g. few-shot)")
    ap.add_argument("--b", type=Path, required=True, help="arm B rows (e.g. zero-shot)")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    labels = json.loads((M.ROOT / "reports/intent/labels/labels.json").read_text(encoding="utf-8"))
    ra = [json.loads(ln) for ln in a.a.open(encoding="utf-8") if ln.strip()]
    rb = [json.loads(ln) for ln in a.b.open(encoding="utf-8") if ln.strip()]
    res = {t: paired_task(ra, rb, labels["labels"]["test"][t], t) for t in M.TASKS}
    a.out.write_text(
        json.dumps({"a": a.a.stem, "b": a.b.stem, "tasks": res}, indent=1) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
