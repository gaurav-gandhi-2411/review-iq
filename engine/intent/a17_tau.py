"""Amendment 17: choose the needs_action routing floor tau for R1+ from VALIDATION data only, by the registered rule.

    PYTHONPATH=. python -m engine.intent.a17_tau --val16-logits D:/ml-cache/s31/run2_val16_out/out/logits.npz --out reports/intent/a17/tau.json

R1+ = R1 (engine first, Amendment 14) OR p(needs_action = yes) >= tau, on the SERVED model (run 2, static int8, x64 thresholds).
Rule: tau = the LARGEST value on the grid 0.100, 0.095, ..., 0.010 for which (a) R1+ recall on gold needs_action = yes over V and T together
is >= 0.97 and (b) R1+ routed share on V is <= 0.30. V = the scale validation split (460, natural mix; probabilities from the committed
pred files). T = the Amendment 16 targeted validation set val16 (460; logits from a Kaggle CPU kernel with the pinned numerics). No sealed
item is read. Writes the chosen tau and the whole grid.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from app.core.classify import runtime

from engine.intent.decide import decide
from engine.intent.fresh16_eval import decide_all

PRED = Path("reports/intent/run2_cpu/onnx_static_int8_x64")
THRESHOLDS = Path("app/core/classify/thresholds_run2_onnx_static_int8_x64.json")
GRID = [round(0.100 - 0.005 * k, 3) for k in range(19)]  # 0.100 ... 0.010
RECALL_MIN = 0.97
SHARE_MAX = 0.30


def r1_val(th: runtime.Thresholds) -> tuple[list[str], np.ndarray, np.ndarray]:
    """V: (ids, R1 route, p_yes) from the committed pred files' val split."""
    probs, ids = {}, None
    for t in ("needs_action", "urgency"):
        d = json.loads((PRED / f"pred_{t}.json").read_text(encoding="utf-8"))
        ids = d["splits"]["val"]["ids"]
        probs[t] = np.array(d["splits"]["val"]["probs"]).reshape(len(ids), len(th.tasks[t].classes))
    na, urg = th.tasks["needs_action"], th.tasks["urgency"]
    c_na, c_u = (
        decide(probs["needs_action"], na.classes, na.rare),
        decide(probs["urgency"], urg.classes, urg.rare),
    )
    route, pyes = [], []
    for i in range(len(ids)):
        na_lab = (
            runtime.ABSTAINED
            if probs["needs_action"][i].max() < na.abstain_below
            else na.classes[c_na[i]]
        )
        u_lab = (
            runtime.ABSTAINED
            if probs["urgency"][i].max() < urg.abstain_below
            else urg.classes[c_u[i]]
        )
        route.append(
            na_lab in ("yes", runtime.ABSTAINED)
            or urg.classes[c_u[i]] == "high"
            or u_lab == runtime.ABSTAINED
        )
        pyes.append(float(probs["needs_action"][i][na.classes.index("yes")]))
    return list(ids), np.array(route), np.array(pyes)


def r1_logits(th: runtime.Thresholds, logits: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    dec = decide_all(th, logits)
    route = [x["needs_action"]["label"] in ("yes", runtime.ABSTAINED) or x["urgency"]["candidate_label"] == "high"
             or x["urgency"]["label"] == runtime.ABSTAINED for x in dec]  # fmt: skip
    return np.array(route), np.array(
        [float(x["needs_action"]["probabilities"]["yes"]) for x in dec]
    )


def choose(gold: np.ndarray, route: np.ndarray, pyes: np.ndarray, in_v: np.ndarray) -> dict:
    rows = []
    for tau in GRID:
        r = route | (pyes >= tau)
        rec = float(r[gold].mean())
        share = float(r[in_v].mean())
        rows.append({"tau": tau, "recall_VT": round(rec, 4), "k": int(r[gold].sum()), "n": int(gold.sum()), "routed_share_V": round(share, 4),
                     "eligible": rec >= RECALL_MIN and share <= SHARE_MAX})  # fmt: skip
    eligible = [r["tau"] for r in rows if r["eligible"]]
    base = {
        "recall_VT": round(float(route[gold].mean()), 4),
        "routed_share_V": round(float(route[in_v].mean()), 4),
    }
    return {"tau": max(eligible) if eligible else None, "r1_registered": base, "grid": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--val16-logits", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    th = runtime.load_thresholds(THRESHOLDS)
    labels = json.loads(Path("reports/intent/labels/labels.json").read_text(encoding="utf-8"))[
        "labels"
    ]["val"]["needs_action"]
    labels16 = json.loads(
        Path("reports/intent/labels_fresh16/train16_labels.json").read_text(encoding="utf-8")
    )["labels"]["needs_action"]
    v_ids, v_route, v_p = r1_val(th)
    z = np.load(a.val16_logits)
    t_ids = [str(x) for x in z["ids"]]
    t_route, t_p = r1_logits(th, z["pinned"])
    gold = np.array(
        [labels.get(i) == "yes" for i in v_ids] + [labels16.get(i) == "yes" for i in t_ids]
    )
    res = choose(
        gold,
        np.concatenate([v_route, t_route]),
        np.concatenate([v_p, t_p]),
        np.array([True] * len(v_ids) + [False] * len(t_ids)),
    )
    res |= {
        "rule": f"largest tau on the grid with recall(V+T) >= {RECALL_MIN} and routed share(V) <= {SHARE_MAX}",
        "n_V": len(v_ids),
        "n_T": len(t_ids),
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("tau", "r1_registered")}))


if __name__ == "__main__":
    main()
