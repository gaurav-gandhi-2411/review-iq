"""Which int8 variant to serve: agreement of each int8 variant with fp32_cpu under the SERVED decision rule, on the sealed test split.

    PYTHONPATH=. python scripts/classify_variant_agreement.py [--out result.json]

The decision rule is the endpoint's (rare-class threshold from `engine.intent.decide`, then abstention when the calibrated
max-softmax is below the conservative p0.95 validation threshold), applied to the committed pred files of
`reports/intent/run2_cpu/<variant>/`. Thresholds are refit per variant on VALIDATION only, as the evaluator does.
Agreement = share of pred-file test items (763, before the leakage exclusions) whose decision (class or abstained) equals fp32's.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from engine.intent.decide import decide, fit_thresholds, rare_for
from engine.intent.evaluate import val_threshold

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "reports/intent/run2_cpu"
LABELS = json.loads((ROOT / "reports/intent/labels/labels.json").read_text(encoding="utf-8"))
TASKS = ("needs_action", "broad_intent", "sentiment", "urgency")
VARIANTS = ("int8_linear", "int8_full", "int8_perchannel", "int8_perchannel_full", "int8_ffn_full")


def decisions(variant: str, task: str) -> np.ndarray:
    """Final decision per test item: class index, or -1 for abstained."""
    d = json.loads((RUN / variant / f"pred_{task}.json").read_text(encoding="utf-8"))
    cl, ids = d["classes"], d["splits"]["val"]["ids"]
    lv = LABELS["labels"]["val"][task]
    sel = [j for j, i in enumerate(ids) if i in lv]
    pv = np.array(d["splits"]["val"]["probs"]).reshape(len(ids), len(cl))[sel]
    yv = np.array([cl.index(lv[ids[j]]) for j in sel])
    pt = np.array(d["splits"]["test"]["probs"]).reshape(-1, len(cl))
    tau = val_threshold(pv.argmax(1) == yv, pv.max(1), 0.95)
    return np.where(pt.max(1) < tau, -1, decide(pt, cl, fit_thresholds(yv, pv, cl, rare_for(task))))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    a = ap.parse_args()
    res = {}
    for v in VARIANTS:
        per = {
            t: round(float((decisions(v, t) == decisions("fp32_cpu", t)).mean()), 4) for t in TASKS
        }
        res[v] = {"per_task": per, "mean": round(float(np.mean(list(per.values()))), 4)}
    print(json.dumps(res, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
