"""Seed-level comparison of run 3 (safety stratum in training, arm B) against the run 2 recipe (arm A), spec Amendment 8.

    python -m engine.intent.seeds aggregate --runs reports/intent/run3_seeds/runs --out reports/intent/run3_seeds/aggregate.json

Input: one `<arm>_seed<N>.json` per trained run (`engine.intent.seed_runs`): per model variant, Evaluation A macro-F1 per core task
(default decisioning and the argmax ablation) and Evaluation B (`urgency=high` recall/precision at D, S and P). Output: per variant and
metric, the per-seed values of each arm, mean, sd, the 95 percent t-interval over seeds, and the paired (same seed) B - A difference with
its own t-interval; plus the pre-registered decision rule applied to the fp32 GPU model at point S. Conclusions are seed-level only.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
from scipy import stats

from engine.intent.run3_compare import CORE

HEADLINE = "fp32_gpu"  # the model the decision rule is applied to (Amendment 8)
S_OP = "S_val_f1_threshold"
TOL_A, PREC_FLOOR = -0.02, -0.05  # Amendment 8 criteria 2 and 3


def interval(vals: list[float]) -> dict:
    """Mean, sample sd and the 95 percent t-interval (df = n - 1) over seeds; interval None below two seeds."""
    v = np.array([x for x in vals if x is not None and not math.isnan(x)], dtype=float)
    n = len(v)
    out: dict = {"n_seeds": n, "mean": None, "sd": None, "t95": None}
    if n:
        out["mean"] = round(float(v.mean()), 4)
    if n >= 2:
        sd = float(v.std(ddof=1))
        h = float(stats.t.ppf(0.975, n - 1)) * sd / math.sqrt(n)
        out["sd"], out["t95"] = round(sd, 4), [round(float(v.mean()) - h, 4), round(float(v.mean()) + h, 4)]
    return out


def metrics_of(run: dict, variant: str) -> dict[str, float | None]:
    """Flat metric name -> value for one run and variant."""
    v = run["variants"][variant]
    flat = {f"A/{mode}/{t}": x for mode, d in v["A"].items() for t, x in d.items()}
    flat |= {f"B/{op}/{k}": x for op, d in v["B"].items() for k, x in d.items() if k in ("recall", "precision")}
    return flat


def aggregate(runs: list[dict]) -> dict:
    """runs = per-run summaries. A seed missing from either arm is dropped from both (paired design)."""
    by = {a: {r["seed"]: r for r in runs if r["arm"] == a} for a in ("A", "B")}
    seeds = sorted(set(by["A"]) & set(by["B"]))
    out: dict = {"seeds": seeds, "dropped_unpaired": sorted(set(by["A"]) ^ set(by["B"])), "variants": {}}
    for variant in by["A"][seeds[0]]["variants"]:
        flat = {a: {s: metrics_of(by[a][s], variant) for s in seeds} for a in by}
        table = {}
        for m in flat["A"][seeds[0]]:
            va, vb = [flat["A"][s][m] for s in seeds], [flat["B"][s][m] for s in seeds]
            diff = [None if x is None or y is None else y - x for x, y in zip(va, vb, strict=True)]
            table[m] = {
                "A": {"per_seed": va, **interval(va)}, "B": {"per_seed": vb, **interval(vb)},
                "B_minus_A": {"per_seed": [None if d is None else round(d, 4) for d in diff], **interval(diff)},
            }  # fmt: skip
        out["variants"][variant] = table
    out["decision"] = decision(out["variants"][HEADLINE]) if HEADLINE in out["variants"] else None
    return out


def decision(table: dict) -> dict:
    """Amendment 8: PROMOTED / NOT PROMOTED / INCONCLUSIVE from the paired seed-level statistics of the fp32 GPU model at point S."""
    rec, prec = table[f"B/{S_OP}/recall"]["B_minus_A"], table[f"B/{S_OP}/precision"]["B_minus_A"]
    base = {t: table[f"A/threshold/{t}"]["B_minus_A"]["mean"] for t in CORE}
    c1 = rec["t95"] is not None and rec["t95"][0] > 0
    c2 = all(d is not None and d >= TOL_A for d in base.values())
    c3 = prec["t95"] is not None and prec["t95"][0] >= PREC_FLOOR
    refuted = (
        not c2 or (rec["t95"] is not None and rec["t95"][1] <= 0)
        or (prec["t95"] is not None and prec["t95"][1] < PREC_FLOOR)
    )  # fmt: skip
    return {
        "model": HEADLINE, "operating_point": S_OP,
        "c1_recall_interval_lower_bound_above_0": {"pass": bool(c1), "paired_diff": rec},
        "c2_base_macro_f1_mean_diff_ge_minus_0.02_every_task": {"pass": bool(c2), "mean_diffs": base},
        "c3_precision_interval_lower_bound_ge_minus_0.05": {"pass": bool(c3), "paired_diff": prec},
        "outcome": "PROMOTED" if c1 and c2 and c3 else "NOT PROMOTED" if refuted else "INCONCLUSIVE",
    }  # fmt: skip


def load_runs(d: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*_seed*.json"))]


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("aggregate")
    g.add_argument("--runs", required=True, help="directory of <arm>_seed<N>.json summaries")
    g.add_argument("--out", required=True)
    a = ap.parse_args()
    res = aggregate(load_runs(Path(a.runs)))
    Path(a.out).write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(res["decision"], indent=1))


if __name__ == "__main__":
    main()
