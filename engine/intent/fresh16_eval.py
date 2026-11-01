"""Amendment 16: score the fresh sealed set `fresh16` ONCE and compute criteria E1 to E6, computed, not written.

    PYTHONPATH=. python -m engine.intent.fresh16_eval --manifest reports/labelling/fresh16/manifest.json \
        --labels16 reports/intent/labels_fresh16/fresh16_labels.json \
        --run4-logits out/fresh16_run4.npz --run4-thresholds out/thresholds_run4.json \
        --run2-logits out/fresh16_run2.npz --run2-thresholds app/core/classify/thresholds_run2_onnx_static_int8_x64.json \
        --llm-rows <llm shards dir> --out reports/intent/fresh16/result.json

Gold = the four-judge consensus (at least 3 of 4) per task, silver. P = gold needs_action=yes. Decisions come from `runtime.decisions` on the logits with each model's
own validation-fitted thresholds, router R1 exactly as Amendment 14. Estimation sets (Amendment 16): Block A alone for the overall criteria; S4 = stars >= 4 items of
Blocks A and B; SL = tokens > 128 items of Blocks A and C. Never mixed otherwise (Block B items were selected on stars, Block C on length).
E1 A-recall Wilson lower >= 0.90; E2 S4 recall lower >= 0.85; E3 SL recall lower >= 0.85; E4 Block A routed share <= 0.30; E5 per served task macro-F1(run4) - macro-F1(run2)
>= -0.02 on Block A items with consensus (prediction = the `decide` candidate label, abstention ignored, as in training); E6 non-inferiority to the gpt-oss-20b zero-shot
needs_action answer on Block A, paired item-level bootstrap lower bound of recall_R - recall_L > -0.05. A set with fewer gold-yes items than the size rule asks is
reported "insufficient data" for its criterion, neither passed nor failed. No review text is read or written.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from app.core.classify import encoder_first as EF
from app.core.classify import runtime

from engine import metrics as M
from engine.intent.fresh15_eval import load_llm, paired_delta, rate
from engine.intent.quantize_m1 import SERVED, split_logits

E1_LOWER, E2_LOWER, E3_LOWER = 0.90, 0.85, 0.85
E4_MAX_SHARE = 0.30
E5_MAX_DROP = 0.02
E6_MARGIN = 0.05
MIN_POS = {"A": 200, "S4": 120, "SL": 120}  # the size rule's gold-yes counts
LONG = 128


def subsets(items: list[dict]) -> dict[str, list[int]]:
    """Indices (into `items`) of the three estimation sets."""
    return {
        "A": [k for k, r in enumerate(items) if r["block"] == "A"],
        "S4": [k for k, r in enumerate(items) if r["block"] in ("A", "B") and r["stars"] >= 4],
        "SL": [k for k, r in enumerate(items) if r["block"] in ("A", "C") and r["ntok"] > LONG],
    }


def decide_all(th: runtime.Thresholds, logits: np.ndarray) -> list[dict]:
    sizes = {t: len(th.tasks[t].classes) for t in SERVED}
    return runtime.decisions(th, split_logits(logits, sizes))


def recall_set(route: np.ndarray, gold_yes: np.ndarray, idx: list[int]) -> dict:
    pos = [k for k in idx if gold_yes[k]]
    return rate(int(route[pos].sum()), len(pos))


def macro_f1_by_task(
    items_idx: list[int], ids: list[str], labels: dict, dec: list[dict], th: runtime.Thresholds
) -> dict[str, float]:
    out = {}
    for t in SERVED:
        cl = list(th.tasks[t].classes)
        lab = labels["labels"][t]
        sel = [k for k in items_idx if ids[k] in lab]
        y = np.array([cl.index(lab[ids[k]]) for k in sel])
        p = np.array([cl.index(dec[k][t]["candidate_label"]) for k in sel])
        out[t] = float(M.macro_f1(y, p))
    return out


def bootstrap_f1_diff(
    items_idx, ids, labels, dec4, dec2, th4, th2, n_boot: int = 1000, seed: int = 42
) -> dict[str, list[float]]:  # noqa: ANN001
    rng = np.random.default_rng(seed)
    out: dict[str, list[float]] = {}
    for t in SERVED:
        lab = labels["labels"][t]
        sel = np.array([k for k in items_idx if ids[k] in lab])
        c4, c2 = list(th4.tasks[t].classes), list(th2.tasks[t].classes)
        y = np.array([c4.index(lab[ids[k]]) for k in sel])
        p4 = np.array([c4.index(dec4[k][t]["candidate_label"]) for k in sel])
        p2 = np.array([c2.index(dec2[k][t]["candidate_label"]) for k in sel])
        diffs = []
        for _ in range(n_boot):
            b = rng.integers(0, len(sel), len(sel))
            diffs.append(M.macro_f1(y[b], p4[b]) - M.macro_f1(y[b], p2[b]))
        out[t] = [
            round(float(np.percentile(diffs, 2.5)), 4),
            round(float(np.percentile(diffs, 97.5)), 4),
        ]
    return out


def evaluate(
    items: list[dict],
    labels: dict,
    th4: runtime.Thresholds,
    lg4: np.ndarray,
    th2: runtime.Thresholds,
    lg2: np.ndarray,
    llm: dict[str, dict],
    n_boot: int = 1000,
) -> dict:
    ids = [r["id"] for r in items]
    dec4, dec2 = decide_all(th4, lg4), decide_all(th2, lg2)
    r4 = np.array([EF.route_r1(d).route for d in dec4])
    r2 = np.array([EF.route_r1(d).route for d in dec2])
    na = labels["labels"]["needs_action"]
    gold_yes = np.array([na.get(i) == "yes" for i in ids])
    sets = subsets(items)
    n_pos = {k: int(sum(gold_yes[j] for j in v)) for k, v in sets.items()}
    insufficient = {k: n_pos[k] < MIN_POS[k] for k in sets}
    rec = {
        k: {"run4": recall_set(r4, gold_yes, sets[k]), "run2": recall_set(r2, gold_yes, sets[k])}
        for k in sets
    }

    def crit(key: str, lower: float) -> dict:
        w = rec[key]["run4"]["wilson95"]
        met = None if insufficient[key] or not w else bool(w[0] >= lower)
        return {
            "recall_run4": rec[key]["run4"],
            "recall_run2": rec[key]["run2"],
            "n_gold_yes": n_pos[key],
            "lower_bound_required": lower,
            "insufficient_data": insufficient[key],
            "met": met,
        }

    a_idx = sets["A"]
    share4, share2 = rate(int(r4[a_idx].sum()), len(a_idx)), rate(int(r2[a_idx].sum()), len(a_idx))
    e4_met = bool(share4["rate"] <= E4_MAX_SHARE)
    f4, f2 = (
        macro_f1_by_task(a_idx, ids, labels, dec4, th4),
        macro_f1_by_task(a_idx, ids, labels, dec2, th2),
    )
    ci = bootstrap_f1_diff(a_idx, ids, labels, dec4, dec2, th4, th2, n_boot)
    e5 = {
        t: {
            "run4": round(f4[t], 4),
            "run2": round(f2[t], 4),
            "diff": round(f4[t] - f2[t], 4),
            "boot95": ci[t],
            "met": bool(f4[t] - f2[t] >= -E5_MAX_DROP),
        }
        for t in SERVED
    }
    pos_a = [k for k in a_idx if gold_yes[k]]
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
    d = paired_delta(r4[pos_a].astype(float), llm_yes.astype(float)) if pos_a else None
    e6_met = bool(d and d["boot95"][0] > -E6_MARGIN)
    verdict_parts = {
        "E1": crit("A", E1_LOWER)["met"], "E2": crit("S4", E2_LOWER)["met"], "E3": crit("SL", E3_LOWER)["met"],
        "E4": e4_met, "E5": all(v["met"] for v in e5.values()), "E6": e6_met,
    }  # fmt: skip
    return {
        "n_items": len(ids),
        "sets": {k: {"n_items": len(v), "n_gold_yes": n_pos[k], "min_gold_yes_required": MIN_POS[k]} for k, v in sets.items()},
        "E1_overall_recall_block_A": crit("A", E1_LOWER),
        "E2_recall_stars_ge_4": crit("S4", E2_LOWER),
        "E3_recall_tokens_gt_128": crit("SL", E3_LOWER),
        "E4_routed_share_block_A": {"run4": share4, "run2": share2, "max": E4_MAX_SHARE, "met": e4_met},
        "E5_macro_f1_run4_minus_run2_block_A": {"max_drop": E5_MAX_DROP, "tasks": e5},
        "E6_non_inferior_to_llm_block_A": {"llm_recall": rate(int(llm_yes.sum()), len(pos_a)), "delta_run4_minus_llm": d, "margin": E6_MARGIN, "llm_missing_rows": int(sum(1 for k in pos_a if not (llm.get(ids[k]) and llm[ids[k]].get("ans")))), "met": e6_met},
        "verdict": {**verdict_parts, "criteria_met": all(v is True for v in verdict_parts.values()), "failing": [k for k, v in verdict_parts.items() if v is False], "insufficient": [k for k, v in verdict_parts.items() if v is None]},
        "report_only": {
            "recall_by_category_block_A": {c: {"run4": recall_set(r4, gold_yes, [k for k in a_idx if items[k]["category"] == c]), "run2": recall_set(r2, gold_yes, [k for k in a_idx if items[k]["category"] == c])} for c in sorted({r["category"] for r in items})},
            "capacity_multiplier_run4_by_open_rate": {
                str(o): (round(EF.capacity_multiplier(float(r4[a_idx].mean()), o), 3) if r4[a_idx].any() or o > 0 else None) for o in (0.0, 0.1, 0.3)
            },
        },
    }  # fmt: skip


def load_items(manifest: Path) -> list[dict]:
    return json.loads(manifest.read_text(encoding="utf-8"))["items"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--labels16", type=Path, required=True)
    ap.add_argument("--run4-logits", type=Path, required=True)
    ap.add_argument("--run4-thresholds", type=Path, required=True)
    ap.add_argument("--run2-logits", type=Path, required=True)
    ap.add_argument("--run2-thresholds", type=Path, default=runtime.THRESHOLDS_ONNX_PATH)
    ap.add_argument("--llm-rows", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    items = load_items(a.manifest)
    z4, z2 = np.load(a.run4_logits), np.load(a.run2_logits)
    ids = [r["id"] for r in items]
    assert list(z4["ids"]) == ids and list(z2["ids"]) == ids, (
        "logits are for different items than the manifest"
    )
    res = evaluate(
        items, json.loads(a.labels16.read_text(encoding="utf-8")),
        runtime.load_thresholds(a.run4_thresholds), z4["logits"], runtime.load_thresholds(a.run2_thresholds), z2["logits"], load_llm(a.llm_rows),
    )  # fmt: skip
    res = {
        "spec": "docs/specs/review-intent.md Amendment 16",
        "run4_thresholds_sha256": runtime.load_thresholds(a.run4_thresholds).sha256,
        **res,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1, default=str) + "\n", encoding="utf-8")
    print(json.dumps(res["verdict"]))


if __name__ == "__main__":
    main()
