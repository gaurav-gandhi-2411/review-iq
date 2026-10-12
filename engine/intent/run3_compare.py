"""Run 3 versus run 2 on the Amendment 5 sealed set (paired) and the Amendment 7 verdict.

    python -m engine.intent.run3_compare --run run2=reports/intent/run2 --run run2_local=reports/intent/run2_local \
        --run run3=reports/intent/run3 --baseline run2 --labels reports/intent/labels/labels.json \
        --manifest reports/labelling/scale/manifest.json --safety reports/intent/run3/safety_eval.json \
        --out reports/intent/run3/comparison.json

Base set: the same sealed items (ledger exclusions dropped) for every run; for each core task the macro-F1 over classes with at
least 20 sealed positives, and the PAIRED item-bootstrap 95 percent interval of the difference to the baseline (same resamples for
both runs, 1,000 resamples, seed 42; item bootstrap, no product ids). 'Outside noise' = that interval excludes 0; seed-to-seed
variance is not measured. The verdict applies the Amendment 7 criteria exactly: A = every core task's point difference
(run 3 minus run 2) >= -0.02; B = urgency=high recall at operating point P above run 2's with a group-bootstrap interval that
excludes 0, and precision not lower by more than 0.05. Both -> PROMOTED, otherwise NEGATIVE RESULT.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from engine import metrics as M
from engine.intent.evaluate import MIN_POSITIVES, SEED, _f1_matrix, drop_excluded, task_arrays

CORE = ("needs_action", "broad_intent", "sentiment", "urgency")
TOL_A = -0.02  # Amendment 7 criterion A
PREC_B = -0.05  # Amendment 7 criterion B
ENV_CONFOUND = 0.02  # Amendment 7: run2_local vs run 2


def load_run(d: Path, manifest: dict, excluded: set[str]) -> dict[str, dict]:
    preds = {}
    for f in sorted(d.glob("pred_*.json")):
        p = json.loads(f.read_text(encoding="utf-8"))
        preds[p["task"]] = p
    drop_excluded(preds, manifest, excluded)
    return preds


def macro_arrays(labels: dict, p: dict, task: str) -> tuple[list[str], np.ndarray, np.ndarray]:
    ids, y, probs = task_arrays(labels, p, task)["test"]
    return ids, y, probs.argmax(1)


def compare_task(
    labels: dict, runs: dict[str, dict], baseline: str, task: str, n_boot: int
) -> dict:
    arr = {n: macro_arrays(labels, r[task], task) for n, r in runs.items()}
    ids, y, _ = arr[baseline]
    assert all(a[0] == ids for a in arr.values()), "runs must score the same sealed items"
    k = len(labels["classes"][task])
    scored = [c for c in range(k) if int((y == c).sum()) >= MIN_POSITIVES]
    idx = np.random.default_rng(SEED).integers(0, len(y), (n_boot, len(y)))

    def boot(pred: np.ndarray) -> np.ndarray:
        return np.nanmean(_f1_matrix(y[idx], pred[idx], k)[:, scored], axis=1)

    base_b, out = (
        boot(arr[baseline][2]),
        {"n": len(y), "scored_classes": [labels["classes"][task][c] for c in scored], "runs": {}},
    )
    for n, (_, _, pred) in arr.items():
        mf = round(float(M.macro_f1(y, pred, scored)), 4) if len(scored) >= 2 else None
        r: dict = {"macro_f1": mf, "accuracy": round(float((pred == y).mean()), 4)}
        if n != baseline and mf is not None:
            d = boot(pred) - base_b
            lo, hi = (round(float(x), 4) for x in np.nanpercentile(d, [2.5, 97.5]))
            base_mf = round(float(M.macro_f1(y, arr[baseline][2], scored)), 4)
            r |= {
                "diff_vs_baseline": round(mf - base_mf, 4),
                "diff_paired_item_boot95": [lo, hi],
                "outside_noise": bool(lo > 0 or hi < 0),
            }
        out["runs"][n] = r
    return out


def verdict(base: dict, safety: dict, baseline: str, challenger: str, control: str | None) -> dict:
    diffs = {
        t: base[t]["runs"][challenger].get("diff_vs_baseline")
        for t in CORE
        if base[t]["runs"].get(challenger, {}).get("macro_f1") is not None
    }
    a_pass = len(diffs) == len(CORE) and all(d >= TOL_A for d in diffs.values())
    p = safety["paired_vs_baseline"][challenger]["P_argmax"]
    b_recall = p["recall_diff_group_boot95"][0] > 0
    b_prec = p["precision_diff"] is not None and p["precision_diff"] >= PREC_B
    env = None
    if control:
        cd = [
            base[t]["runs"][control].get("diff_vs_baseline")
            for t in CORE
            if base[t]["runs"].get(control, {}).get("diff_vs_baseline") is not None
        ]
        env = bool(any(abs(x) > ENV_CONFOUND for x in cd))
    return {
        "A_nondegradation": {"pass": a_pass, "tolerance": TOL_A, "core_macro_f1_diff_vs_run2": diffs},
        "B_safety_gain": {"pass": bool(b_recall and b_prec), "recall_diff_ci_excludes_0_above": bool(b_recall), "precision_diff_ok": bool(b_prec),
                          "recall_diff": p["recall_diff"], "recall_diff_group_boot95": p["recall_diff_group_boot95"], "precision_diff": p["precision_diff"]},
        "environment_confounded": env,
        "outcome": "PROMOTED" if a_pass and b_recall and b_prec else "NEGATIVE RESULT (not promoted)",
    }  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="append", required=True, help="name=pred dir, repeatable")
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--challenger", default="run3")
    ap.add_argument("--control", default="run2_local")
    ap.add_argument("--labels", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--ledger", default="reports/labelling/ledger.json")
    ap.add_argument("--safety", required=True, help="safety_eval.json")
    ap.add_argument("--n-boot", type=int, default=1000)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    load = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))  # noqa: E731
    labels, manifest = load(a.labels), load(a.manifest)
    excluded = set(load(a.ledger).get("sealed_excluded", []))
    runs = {
        kv.split("=", 1)[0]: load_run(Path(kv.split("=", 1)[1]), manifest, excluded) for kv in a.run
    }
    base = {t: compare_task(labels, runs, a.baseline, t, a.n_boot) for t in CORE}
    safety = load(a.safety)
    res = {
        "bootstrap": f"{a.n_boot} paired resamples of ITEMS, seed {SEED}, no product ids; seed-to-seed variance not measured",
        "baseline": a.baseline, "base_sealed_set": base,
        "verdict": verdict(base, safety, a.baseline, a.challenger, a.control if a.control in runs else None),
    }  # fmt: skip
    Path(a.out).write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(res["verdict"], indent=1))


if __name__ == "__main__":
    main()
