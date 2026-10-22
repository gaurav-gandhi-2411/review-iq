"""Amendment 14 measurement: encoder-first routing share, capacity multiplier, and the urgent-review recall criteria C1 to C4.

    PYTHONPATH=. python -m engine.intent.encoder_first_eval --artifacts D:/ml-cache/s25/classify_artifacts_onnx \
        --safety-texts D:/ml-cache/s23/safety_items.jsonl --llm-rows <safety LLM shards dir> --out reports/intent/encoder_first/result.json

Inputs (all served-model decisions, thresholds committed, nothing re-fitted):
  sealed set  : the committed ONNX pred files (served_per_item parity with the offline reference is 0 mismatches), rule R1/R2 applied.
  safety test : the ONNX model run here on the private safety TEST texts (served decisions), R1/R2 applied; unit of analysis is the
                independent near-duplicate GROUP exactly as `engine/intent/safety_eval.py`; ENRICHED, SILVER labels, never a base rate.
  LLM path    : gpt-oss-20b zero-shot rows (Amendment 12 settings) for the same safety test items. No review text is written.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from app.core.classify import encoder_first as EF
from app.core.classify import runtime

from engine.intent.decide import decide
from engine.intent.safety_eval import HIGH, aggregates, counts, load_context, point
from engine.labelling.repilot2_report import wilson

ROOT = Path(__file__).resolve().parents[2]
PRED = (
    ROOT / "reports/intent/run2_cpu/onnx_static_int8_x64"
)  # S30: production numerics; --pred-dir reproduces the S28 run
CAP = ROOT / "eval/results/capacity_model.json"
ROUTE_TASKS = ("needs_action", "urgency")
O_RATES = (0.0, 0.1, 0.3)
STARTER, GROWTH = 5000, 25000


def sealed_tasks(th: runtime.Thresholds) -> tuple[list[str], list[dict]]:
    """Served-equivalent decisions on the pred-file test ids: [{task: {label, candidate_label}}] aligned with the id list."""
    per_task, ids = {}, None
    for t in ROUTE_TASKS:
        d = json.loads((PRED / f"pred_{t}.json").read_text(encoding="utf-8"))
        ids = d["splits"]["test"]["ids"]
        spec = th.tasks[t]
        p = np.array(d["splits"]["test"]["probs"]).reshape(len(ids), len(spec.classes))
        cand = decide(p, spec.classes, spec.rare)
        per_task[t] = [
            {
                "label": runtime.ABSTAINED
                if p[i].max() < spec.abstain_below
                else spec.classes[cand[i]],
                "candidate_label": spec.classes[cand[i]],
            }
            for i in range(len(ids))
        ]
    assert ids is not None
    return ids, [{t: per_task[t][i] for t in ROUTE_TASKS} for i in range(len(ids))]


def rate(k: int, n: int) -> dict:
    return {
        "k": k,
        "n": n,
        "rate": round(k / n, 4) if n else None,
        "wilson95": [round(x, 4) for x in wilson(k, n)] if n else None,
    }


def capacity(f: float) -> dict:
    cm = json.loads(CAP.read_text(encoding="utf-8"))
    hard = cm["real_extractions_per_day_ceiling"]
    out: dict = {
        "source": "eval/results/capacity_model.json",
        "binding_tier": cm["binding_tier_per_day"],
        "ceiling_per_day_hard_limit": round(hard, 1),
        "ceiling_per_day_100k_budget": round(hard / 2, 1),
        "per_month_hard_limit": round(hard * 30),
        "per_month_100k_budget": round(hard * 15),
        "tokens_per_extraction": {
            k: round(v["mean_tokens_per_extraction"], 1) for k, v in cm["per_tier_bindings"].items()
        },
        "traffic_mix": {
            k: round(v["fraction_of_traffic"], 4) for k, v in cm["per_tier_bindings"].items()
        },
        "quota_need_per_day": {
            "starter_5000": round(STARTER / 30, 1),
            "growth_25000": round(GROWTH / 30, 1),
        },
        "by_open_rate": {},
    }
    for o in O_RATES:
        m = EF.capacity_multiplier(f, o)
        out["by_open_rate"][str(o)] = {
            "multiplier": round(m, 3),
            "per_day_hard_limit": round(hard * m, 1),
            "per_day_100k_budget": round(hard / 2 * m, 1),
            "per_month_hard_limit": round(hard * m * 30),
            "per_month_100k_budget": round(hard * m * 15),
            "starter_met_hard": hard * m >= STARTER / 30,
            "starter_met_100k": hard / 2 * m >= STARTER / 30,
            "growth_met_hard": hard * m >= GROWTH / 30,
            "growth_met_100k": hard / 2 * m >= GROWTH / 30,
        }
    out["multiplier_needed"] = {
        "starter_hard": round(STARTER / 30 / hard, 2),
        "starter_100k": round(STARTER / 30 / (hard / 2), 2),
        "growth_hard": round(GROWTH / 30 / hard, 2),
        "growth_100k": round(GROWTH / 30 / (hard / 2), 2),
    }
    return out


def sealed_part(th: runtime.Thresholds) -> dict:
    from engine.intent.llm_moat import sealed_ids

    ids, tasks = sealed_tasks(th)
    sealed = set(sealed_ids())
    labels = json.loads((ROOT / "reports/intent/labels/labels.json").read_text(encoding="utf-8"))[
        "labels"
    ]["test"]
    keep = [i for i, x in enumerate(ids) if x in sealed]
    out: dict = {"n_sealed": len(keep)}
    for name, router in (("R1", EF.route_r1), ("R2", EF.route_r2)):
        dec = [router(tasks[i]) for i in keep]
        k = sum(d.route for d in dec)
        f = k / len(keep)
        out[name] = {"routed": rate(k, len(keep)), "capacity": capacity(f)}
        reasons: dict[str, int] = {}
        for d in dec:
            for r in d.reasons:
                reasons[r] = reasons.get(r, 0) + 1
        out[name]["reason_counts_overlapping"] = reasons
    # recall of the gold classes the router must not drop (R1)
    for task, pos, key in (
        ("needs_action", "yes", "C3_needs_action_yes"),
        ("urgency", HIGH, "C2_urgency_high"),
    ):
        g = labels[task]
        sel = [i for i in keep if ids[i] in g and g[ids[i]] == pos]
        hit = sum(EF.route_r1(tasks[i]).route for i in sel)
        out[key] = {"routing_recall_R1": rate(hit, len(sel))}
    return out


def safety_part(
    artifacts: Path, texts_path: Path, llm_dir: Path | None, n_boot: int = 1000
) -> dict:
    th = runtime.load_thresholds(runtime.THRESHOLDS_ONNX_PATH)
    ctx = load_context(
        str(ROOT / "reports/labelling/safety/labels.jsonl"),
        str(texts_path),
        str(ROOT / "reports/labelling/ledger.json"),
    )
    texts = {
        r["id"]: r["text"]
        for r in map(json.loads, texts_path.read_text(encoding="utf-8").splitlines())
    }
    test_ids = ctx["test_ids"]
    backend = runtime.OnnxBackend(artifacts, th)
    dec = runtime.decisions(th, backend.logits([texts[i] for i in test_ids]))
    keep = [
        j
        for j, i in enumerate(test_ids)
        if ctx["urgency"][i] is not None and ctx["shas"][i] not in ctx["excluded"]
    ]
    ids = [test_ids[j] for j in keep]
    y = np.array([ctx["urgency"][i] == HIGH for i in ids])
    g = np.array([ctx["groups"][i] for i in ids])
    w = counts(len(np.unique(g)), n_boot)
    route = {
        "R1": np.array([EF.route_r1(dec[j]).route for j in keep]),
        "R2": np.array([EF.route_r2(dec[j]).route for j in keep]),
    }
    enc_high = np.array([dec[j]["urgency"]["candidate_label"] == HIGH for j in keep])
    out: dict = {
        "label_basis": "ENRICHED keyword-mined SILVER (panel) labels; independent near-duplicate groups; never pooled with the Amendment 5 set; not a base rate",
        "n_test_items": len(test_ids),
        "n_evaluated": len(ids),
        "independent_positive_groups": int(len({g[i] for i in range(len(ids)) if y[i]})),
        "C1_routing_recall": {n: point(aggregates(y, r, g), y, r, w) for n, r in route.items()},
        "encoder_urgency_high_flag": point(aggregates(y, enc_high, g), y, enc_high, w),
        "routed_share_of_safety_items_R1": rate(int(route["R1"].sum()), len(ids)),
    }
    if llm_dir is not None:
        rows = {}
        for f in sorted(llm_dir.glob("*.jsonl")):
            for ln in f.read_text(encoding="utf-8").splitlines():
                if ln.strip():
                    r = json.loads(ln)
                    rows[r["id"]] = r

        def llm_high(i: str) -> bool:
            r = rows.get(i)
            return bool(r and r.get("ans") and r["ans"].get("urgency", {}).get("label") == HIGH)

        llm = np.array([llm_high(i) for i in ids])
        empty = sum(1 for i in ids if i not in rows or rows[i].get("ans") is None)
        caught = y & llm
        out["llm"] = {
            "rows": len(rows),
            "empty_or_unparseable_or_missing": empty,
            "ended_at_token_cap": sum(1 for r in rows.values() if r.get("fin") == "length"),
            "llm_urgency_high_flag": point(aggregates(y, llm, g), y, llm, w),
            "llm_caught_items": int(caught.sum()),
            "C4_routing_recall_on_llm_caught": {
                n: point(aggregates(caught, r, g), caught, r, w) for n, r in route.items()
            },
            "matched_precision_table": {
                "encoder_urgency_high_decision": point(aggregates(y, enc_high, g), y, enc_high, w),
                "llm_urgency_high_answer": point(aggregates(y, llm, g), y, llm, w),
                "R1_as_routing_flag": point(aggregates(y, route["R1"], g), y, route["R1"], w),
            },
        }
    return out


def main() -> None:
    global PRED
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts", required=True, type=Path)
    ap.add_argument("--safety-texts", required=True, type=Path)
    ap.add_argument("--llm-rows", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--pred-dir", type=Path, default=PRED)
    ap.add_argument("--thresholds", type=Path, default=runtime.THRESHOLDS_ONNX_PATH)
    a = ap.parse_args()
    PRED = a.pred_dir
    th = runtime.load_thresholds(a.thresholds)
    res = {
        "spec": "docs/specs/review-intent.md Amendment 14",
        "thresholds_sha256": th.sha256,
        "sealed_set": sealed_part(th),
        "safety_stratum": safety_part(a.artifacts, a.safety_texts, a.llm_rows),
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
