"""S30: old (laptop numerics, S25/S27/S28) vs new (production numerics, ORT x64quantprecision=1) results side by side.

    python scripts/numerics_rerun_report.py --equivalence D:/ml-cache/s30/equiv/equivalence.json --kaggle-facts D:/ml-cache/s30/kaggle_equiv/out/run_result.json \
        --out-json reports/serving/numerics_rerun_s30.json --out-md reports/serving/numerics_rerun_s30.md

This is a RE-RUN UNDER CORRECTED NUMERICS of results that already existed, not a new result: the model file, the labels, the validation/test
split, the thresholds procedure and the router rule are unchanged; only the arithmetic of the int8 kernels (and therefore the fitted
temperatures and thresholds) differs. Every number is read from a committed artifact or the two private equivalence files; nothing is typed here.
Aggregates only; no review text.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
TASKS = ("needs_action", "broad_intent", "sentiment", "urgency")
OLD = {
    "pred": ROOT / "reports/intent/run2_cpu/onnx_static_int8",
    "eval": ROOT / "reports/intent/eval_run2_onnx_static_int8.json",
    "th": ROOT / "app/core/classify/thresholds_run2_onnx_static_int8.json",
    "a14": ROOT / "reports/intent/encoder_first/result.json",
    "a15": ROOT / "reports/intent/fresh15/result.json",
}
NEW = {
    "pred": ROOT / "reports/intent/run2_cpu/onnx_static_int8_x64",
    "eval": ROOT / "reports/intent/eval_run2_onnx_static_int8_x64.json",
    "th": ROOT / "app/core/classify/thresholds_run2_onnx_static_int8_x64.json",
    "a14": ROOT / "reports/intent/encoder_first_x64/result.json",
    "a15": ROOT / "reports/intent/fresh15_x64/result.json",
}
FP32 = ROOT / "reports/intent/run2_cpu/fp32_cpu"


def _j(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def val_agreement(pred_dir: Path) -> dict[str, float]:
    out = {}
    for t in TASKS:
        a, b = _j(pred_dir / f"pred_{t}.json"), _j(FP32 / f"pred_{t}.json")
        n = len(b["classes"])
        pa = np.array(a["splits"]["val"]["probs"]).reshape(-1, n).argmax(1)
        pb = np.array(b["splits"]["val"]["probs"]).reshape(-1, n).argmax(1)
        out[t] = round(float((pa == pb).mean()), 4)
    out["mean"] = round(float(np.mean([out[t] for t in TASKS])), 4)
    return out


def side(cfg: dict) -> dict:
    th, ev, a14, a15 = _j(cfg["th"]), _j(cfg["eval"]), _j(cfg["a14"]), _j(cfg["a15"])
    cons = {t: ev["tasks"][t]["selective"]["conservative_p0.95"] for t in TASKS}
    o01 = a14["sealed_set"]["R1"]["capacity"]["by_open_rate"]["0.1"]["multiplier"]
    return {
        "val_agreement_with_fp32": val_agreement(cfg["pred"]),
        "thresholds": {
            t: {
                "temperature": round(th["tasks"][t]["temperature"], 4),
                "abstain_below": th["tasks"][t]["abstain_below"],
                "rare": th["tasks"][t]["rare"],
            }
            for t in TASKS
        },  # fmt: skip
        "sealed_headline_p0.95": {
            t: {
                k: cons[t][k]
                for k in ("coverage", "coverage_ci95", "precision", "precision_ci95", "n_answered")
            }
            for t in TASKS
        },
        "amendment14": {
            "routed_share_sealed": a14["sealed_set"]["R1"]["routed"]["rate"],
            "multiplier_open_rate_0.1": o01,
            "C1_safety_urgency_high_recall": a14["safety_stratum"]["C1_routing_recall"]["R1"][
                "recall_wilson95"
            ],
            "C2_sealed_urgency_high": a14["sealed_set"]["C2_urgency_high"]["routing_recall_R1"],
            "C3_sealed_needs_action_yes": a14["sealed_set"]["C3_needs_action_yes"][
                "routing_recall_R1"
            ],
        },
        "amendment15": {
            "A_router_recall": a15["A_router_routing_recall"],
            "llm_recall": a15["llm"]["recall"],
            "B_delta": a15["B_non_inferiority"],
            "routed_share": a15["report_only"]["routed_share_all_items"],
            "criteria_met": a15["criteria_met"],
        },
    }


def render(res: dict) -> str:
    o, n = res["old_laptop_numerics"], res["new_production_numerics"]

    def blk(name: str, body: str) -> str:
        return f"<!-- METRICS:START:{name} -->\n{body}\n<!-- METRICS:END -->"

    th_rows = "\n".join(
        f"| {t} | {o['thresholds'][t]['temperature']} | {n['thresholds'][t]['temperature']} | {o['thresholds'][t]['abstain_below']:.4f} | {n['thresholds'][t]['abstain_below']:.4f} |"
        for t in TASKS
    )
    hl_rows = "\n".join(
        f"| {t} | {o['sealed_headline_p0.95'][t]['coverage']} | {n['sealed_headline_p0.95'][t]['coverage']} | {o['sealed_headline_p0.95'][t]['precision']} | {n['sealed_headline_p0.95'][t]['precision']} |"
        for t in TASKS
    )
    ag = f"mean argmax agreement with the fp32 model on validation: old {o['val_agreement_with_fp32']['mean']}, new {n['val_agreement_with_fp32']['mean']}"
    a14o, a14n, a15o, a15n = o["amendment14"], n["amendment14"], o["amendment15"], n["amendment15"]

    def r(d: dict) -> str:
        return f"{d['rate']} ({d['k']}/{d['n']}) {d['wilson95']}"

    crit = "\n".join(
        [
            "| criterion | old (laptop numerics) | new (production numerics) |",
            "|---|---|---|",
            f"| R1 routed share, sealed 757 | {a14o['routed_share_sealed']} | {a14n['routed_share_sealed']} |",
            f"| multiplier at open rate 0.1 | {a14o['multiplier_open_rate_0.1']} | {a14n['multiplier_open_rate_0.1']} |",
            f"| C1 safety stratum, urgency=high routing recall, Wilson 95 | {a14o['C1_safety_urgency_high_recall']} | {a14n['C1_safety_urgency_high_recall']} |",
            f"| C3 sealed needs_action=yes routing recall | {r(a14o['C3_sealed_needs_action_yes'])} | {r(a14n['C3_sealed_needs_action_yes'])} |",
            f"| Amendment 15 A: fresh-set router recall (>= 0.90 lower bound) | {r(a15o['A_router_recall'])} | {r(a15n['A_router_recall'])} |",
            f"| Amendment 15 B: delta vs LLM, bootstrap 95 (> -0.05) | {a15o['B_delta']['delta']} {a15o['B_delta']['boot95']} | {a15n['B_delta']['delta']} {a15n['B_delta']['boot95']} |",
            f"| Amendment 15 both criteria met | {a15o['criteria_met']} | {a15n['criteria_met']} |",
        ]
    )
    eq = res.get("equivalence", {})
    eq_rows = "\n".join(
        f"| {k} | {v['label_mismatches']} of {v['n_decisions']} | {v['max_abs_prob_diff']} | {v['median_per_review_max_diff']} | {v['p95_per_review_max_diff']} |" for k, v in eq.items()
    )  # fmt: skip
    kf = res.get("kaggle_cpu", {})
    return "\n".join(
        [
            "# Re-run under corrected numerics (S30)",
            "",
            "GG decision: production numerics are the reference (`session.x64quantprecision=1`, `app/core/classify/ort_numerics.py`). This is a RE-RUN of results",
            "that already existed, not a new result: same model file, labels, splits, threshold procedure and router rule; only the int8 kernel arithmetic (and",
            "therefore the fitted temperatures and thresholds) changed. Silver labels throughout. Generated by `scripts/numerics_rerun_report.py`.",
            "",
            "## Numerics equivalence on 200 sealed reviews (4 tasks = 800 decisions)",
            "",
            blk(
                "numerics_equivalence",
                "| pair | label mismatches | max abs prob diff | median per-review max | p95 per-review max |\n|---|---|---|---|---|\n"
                + eq_rows,
            ),
            "",
            f"Kaggle CPU kernel host: {kf.get('cpu_model')} ({kf.get('n_logical_cpus')} logical CPUs), avx2={kf.get('flags_of_interest', {}).get('avx2')}, avx512_vnni={kf.get('flags_of_interest', {}).get('avx512_vnni')}, onnxruntime {kf.get('onnxruntime')}.",
            "",
            "## Validation agreement and thresholds",
            "",
            blk("numerics_validation", ag),
            "",
            blk(
                "numerics_thresholds",
                "| task | temperature old | temperature new | abstain_below old | abstain_below new |\n|---|---|---|---|---|\n"
                + th_rows,
            ),
            "",
            "## Sealed headline at batch 1, conservative p0.95 rule (thresholds fitted on validation)",
            "",
            blk(
                "numerics_headline",
                "| task | coverage old | coverage new | precision old | precision new |\n|---|---|---|---|---|\n"
                + hl_rows,
            ),
            "",
            "## Router criteria (Amendments 14 and 15)",
            "",
            blk("numerics_router", crit),
            "",
        ]
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--equivalence", type=Path, required=True)
    ap.add_argument("--kaggle-facts", type=Path, required=True)
    ap.add_argument("--out-json", type=Path, required=True)
    ap.add_argument("--out-md", type=Path, required=True)
    a = ap.parse_args()
    res = {
        "old_laptop_numerics": side(OLD),
        "new_production_numerics": side(NEW),
        "equivalence": _j(a.equivalence),
        "kaggle_cpu": _j(a.kaggle_facts),
    }
    a.out_json.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    a.out_md.write_text(render(res), encoding="utf-8")
    print("wrote", a.out_json, a.out_md)


if __name__ == "__main__":
    main()
