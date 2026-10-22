"""Write the serving thresholds file for POST /v2/classify from the OFFLINE evaluation artifacts (S24 A6).

    python scripts/build_classify_thresholds.py --pred-dir reports/intent/run2_cpu/int8_ffn_full \
        --eval reports/intent/eval_run2_int8_ffn_full.json \
        --out app/core/classify/thresholds_run2_int8_ffn_full.json

Everything is derived from VALIDATION only: the temperature and the rare-class thresholds are read from the pred files
(`engine.intent.train.write_predictions`), and both thresholds are recomputed with the evaluator's own functions
(`fit_thresholds`, `val_threshold`, conservative rule, target 0.95) on the validation split of the same pred file. The
abstention threshold is cross-checked against the evaluation JSON (the rare-class threshold is not recorded there: the
run-2 artifacts predate Amendment 8), and the script fails loudly if they differ.
No review text is read or written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from engine.intent.decide import fit_thresholds, rare_for
from engine.intent.evaluate import val_threshold

TASKS = ("needs_action", "broad_intent", "sentiment", "urgency")
TARGET = 0.95


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def build(
    pred_dir: Path, labels_path: Path, eval_path: Path, variant: str = "int8_ffn_full"
) -> dict:
    """Thresholds document for the four core tasks (values are python floats, serialised with their shortest repr)."""
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    ev = json.loads(eval_path.read_text(encoding="utf-8"))
    # the checkpoint's head has one output per class of ALL 14 tasks; the four served tasks are its first columns
    if list(labels["classes"])[: len(TASKS)] != list(TASKS):
        raise SystemExit("served tasks are not the first head columns")
    head_outputs = sum(len(c) for c in labels["classes"].values())
    tasks: dict[str, dict] = {}
    for t in TASKS:
        d = json.loads((pred_dir / f"pred_{t}.json").read_text(encoding="utf-8"))
        cl = list(d["classes"])
        ids = d["splits"]["val"]["ids"]
        lv = labels["labels"]["val"][t]
        sel = [j for j, i in enumerate(ids) if i in lv]
        pv = np.array(d["splits"]["val"]["probs"]).reshape(len(ids), len(cl))[sel]
        yv = np.array([cl.index(lv[ids[j]]) for j in sel], dtype=np.int64)
        rare = fit_thresholds(yv, pv, cl, rare_for(t))
        # The run-2 pred files and eval JSON predate Amendment 8 (no "decision" block, argmax-era evaluation), so no
        # rare-class threshold is recorded offline; it is the evaluator's `fit_thresholds` on validation, as above.
        tau = val_threshold(pv.argmax(1) == yv, pv.max(1), TARGET)
        recorded = ev["tasks"][t]["selective"][f"conservative_p{TARGET}"]["threshold"]
        if (None if math.isinf(tau) else round(tau, 6)) != recorded:
            raise SystemExit(f"{t}: abstention threshold {tau} != evaluation JSON {recorded}")
        tasks[t] = {
            "classes": cl, "temperature": d["temperature"], "rare": rare,
            "abstain_below": None if math.isinf(tau) else float(tau),
            "n_validation_labelled": int(len(yv)),
        }  # fmt: skip
    return {
        "schema": 1, "run": "run2", "variant": variant, "max_len": 128, "head_outputs": head_outputs,
        "decision": {
            "mode": "threshold",
            "abstention": f"max calibrated softmax < abstain_below -> 'abstained' (conservative rule, target {TARGET}); "
            "null = never reaches the target on validation -> always abstains",
        },
        "fitted_on": "validation only",
        "source": {
            "pred_files_sha256": {t: _sha(pred_dir / f"pred_{t}.json") for t in TASKS},
            "labels_sha256": _sha(labels_path), "eval_sha256": _sha(eval_path),
        },
        "tasks": tasks,
    }  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred-dir", required=True)
    ap.add_argument("--labels", default="reports/intent/labels/labels.json")
    ap.add_argument("--eval", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--variant", default="int8_ffn_full")
    a = ap.parse_args()
    doc = build(Path(a.pred_dir), Path(a.labels), Path(a.eval), a.variant)
    Path(a.out).write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print("wrote", a.out)


if __name__ == "__main__":
    main()
