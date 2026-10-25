"""Amendment 17: the one registered evaluation of R1+ (R1 OR p(needs_action = yes) >= tau) on the served model over fresh17.

    PYTHONPATH=. python -m engine.intent.fresh17_eval --manifest reports/labelling/fresh17/manifest.json \
        --labels17 reports/intent/labels_fresh17/fresh17_labels.json --run2-logits <fresh17 run-2 logits npz (logits, ids)> \
        --tau-file reports/intent/a17/tau.json --llm-rows <LLM shards dir> --out reports/intent/fresh17/result.json

Criteria (docs/specs/review-intent.md Amendment 17): F1 Block A R1+ recall Wilson lower bound >= 0.90; F2 S4 (A or B, stars >= 4) lower bound
>= 0.85; F3 Block A routed share <= 0.30 (point estimate); F4 lower bound of the paired bootstrap of recall(R1+) minus recall(LLM) on Block A
gold-yes > -0.05. Report only: R1 as registered on the same items, recall by category, capacity multiplier. Ids and numbers only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from app.core.classify import encoder_first as EF
from app.core.classify import runtime

from engine.intent.fresh15_eval import load_llm, paired_delta, rate
from engine.intent.fresh16_eval import decide_all, recall_set

F1_LOWER, F2_LOWER, F3_MAX_SHARE, F4_MARGIN = 0.90, 0.85, 0.30, 0.05
MIN_POS = {"A": 200, "S4": 120}
THRESHOLDS = Path("app/core/classify/thresholds_run2_onnx_static_int8_x64.json")


def routes(th: runtime.Thresholds, logits: np.ndarray, tau: float) -> tuple[np.ndarray, np.ndarray]:
    """(R1, R1+) per item from the served decisions."""
    dec = decide_all(th, logits)
    r1 = np.array([EF.route_r1(d).route for d in dec])
    p_yes = np.array([float(d["needs_action"]["probabilities"]["yes"]) for d in dec])
    return r1, r1 | (p_yes >= tau)


def evaluate(
    items: list[dict],
    labels: dict,
    th: runtime.Thresholds,
    logits: np.ndarray,
    tau: float,
    llm: dict[str, dict],
) -> dict:
    ids = [r["id"] for r in items]
    r1, rp = routes(th, logits, tau)
    na = labels["labels"]["needs_action"]
    gold = np.array([na.get(i) == "yes" for i in ids])
    a_idx = [k for k, r in enumerate(items) if r["block"] == "A"]
    s4_idx = [
        k
        for k, r in enumerate(items)
        if r["block"] in ("A", "B") and r["stars"] is not None and r["stars"] >= 4
    ]
    sets = {"A": a_idx, "S4": s4_idx}
    n_pos = {k: int(gold[v].sum()) for k, v in sets.items()}
    rec = {
        k: {"r1_plus": recall_set(rp, gold, v), "r1": recall_set(r1, gold, v)}
        for k, v in sets.items()
    }

    def crit(key: str, lower: float) -> dict:
        w = rec[key]["r1_plus"]["wilson95"]
        short = n_pos[key] < MIN_POS[key]
        return {"recall_r1_plus": rec[key]["r1_plus"], "recall_r1": rec[key]["r1"], "n_gold_yes": n_pos[key], "lower_bound_required": lower,
                "below_size_rule": short, "met": bool(w and w[0] >= lower)}  # fmt: skip

    share_p, share_1 = (
        rate(int(rp[a_idx].sum()), len(a_idx)),
        rate(int(r1[a_idx].sum()), len(a_idx)),
    )
    pos_a = [k for k in a_idx if gold[k]]
    llm_yes = np.array(
        [
            bool(
                llm.get(ids[k])
                and llm[ids[k]].get("ans")
                and llm[ids[k]]["ans"]["needs_action"]["label"] == "yes"
            )
            for k in pos_a
        ]
    )
    d = paired_delta(rp[pos_a].astype(float), llm_yes.astype(float)) if pos_a else None
    parts = {"F1": crit("A", F1_LOWER)["met"], "F2": crit("S4", F2_LOWER)["met"], "F3": bool(share_p["rate"] <= F3_MAX_SHARE),
             "F4": bool(d and d["boot95"][0] > -F4_MARGIN)}  # fmt: skip
    return {
        "tau": tau, "n_items": len(ids),
        "sets": {k: {"n_items": len(v), "n_gold_yes": n_pos[k], "min_gold_yes_required": MIN_POS[k]} for k, v in sets.items()},
        "F1_recall_block_A": crit("A", F1_LOWER),
        "F2_recall_s4": crit("S4", F2_LOWER),
        "F3_routed_share_block_A": {"r1_plus": share_p, "r1": share_1, "max": F3_MAX_SHARE, "met": parts["F3"]},
        "F4_non_inferior_to_llm_block_A": {"llm_recall": rate(int(llm_yes.sum()), len(pos_a)), "delta_r1_plus_minus_llm": d, "margin": F4_MARGIN,
                                           "llm_missing_rows": int(sum(1 for k in pos_a if not (llm.get(ids[k]) and llm[ids[k]].get("ans")))),
                                           "met": parts["F4"]},
        "verdict": {**parts, "criteria_met": all(parts.values()), "failing": [k for k, v in parts.items() if not v]},
        "report_only": {
            "recall_by_category_block_A": {c: {"r1_plus": recall_set(rp, gold, [k for k in a_idx if items[k]["category"] == c]),
                                               "r1": recall_set(r1, gold, [k for k in a_idx if items[k]["category"] == c])}
                                           for c in sorted({r["category"] for r in items})},
            "capacity_multiplier_r1_plus_by_open_rate": {str(o): round(EF.capacity_multiplier(float(rp[a_idx].mean()), o), 3) for o in (0.0, 0.1, 0.3)},
        },
    }  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--labels17", type=Path, required=True)
    ap.add_argument("--run2-logits", type=Path, required=True)
    ap.add_argument("--tau-file", type=Path, required=True)
    ap.add_argument("--llm-rows", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    items = json.loads(a.manifest.read_text(encoding="utf-8"))["items"]
    z = np.load(a.run2_logits)
    if [str(x) for x in z["ids"]] != [r["id"] for r in items]:
        raise SystemExit("logits are for different items than the manifest")
    tau = json.loads(a.tau_file.read_text(encoding="utf-8"))["tau"]
    th = runtime.load_thresholds(THRESHOLDS)
    res = evaluate(
        items,
        json.loads(a.labels17.read_text(encoding="utf-8")),
        th,
        z["logits"],
        float(tau),
        load_llm(a.llm_rows),
    )
    res = {
        "spec": "docs/specs/review-intent.md Amendment 17",
        "thresholds_sha256": th.sha256,
        **res,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1, default=str) + "\n", encoding="utf-8")
    print(json.dumps(res["verdict"]))


if __name__ == "__main__":
    main()
