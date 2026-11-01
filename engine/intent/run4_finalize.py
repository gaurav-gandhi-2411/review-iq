"""Amendment 16: from a quantised run-4 model to everything the evaluation needs, on the production numerics.

    PYTHONPATH=. python -m engine.intent.run4_finalize --model <dir>/model_m1_auto.onnx --hf <hf dir> --labels labels.json --scale-texts scale.jsonl \
        --arm-dir <chosen fp32 arm dir> --fresh16-texts fresh16.jsonl --max-len 128 --truncation head --variant run4_onnx_x64 --out <dir>

Writes, exactly as `scripts/onnx_pred_files.py` does for run 2 (one review per forward, batch-1 logits, `engine.intent.train.write_predictions`): the pred files for the
validation and test splits of the chosen arm's run (temperature and rare-class thresholds fitted on VALIDATION only), the served-format thresholds file
(`scripts/build_classify_thresholds.build`, no sealed evaluation behind it) and the raw logits of the quantised model on `fresh16` for the one sealed evaluation.
The fresh16 items are scored here but nothing is computed from their labels in this module. Review text is read from private files and never written.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from engine.intent import quantize_m1 as Q


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", type=Path, required=True)
    ap.add_argument("--hf", type=Path, required=True)
    ap.add_argument("--labels", type=Path, required=True)
    ap.add_argument("--scale-texts", type=Path, required=True)
    ap.add_argument(
        "--arm-dir",
        type=Path,
        required=True,
        help="the chosen arm's fp32 training output (pred files give the val/test id order)",
    )
    ap.add_argument("--fresh16-texts", type=Path, required=True)
    ap.add_argument("--max-len", type=int, default=128)
    ap.add_argument("--truncation", default="head", choices=["head", "head_tail"])
    ap.add_argument("--variant", required=True)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    from scripts.build_classify_thresholds import build
    from transformers import AutoTokenizer

    from engine.intent import train as T

    lab = json.loads(a.labels.read_text(encoding="utf-8"))
    classes = {t: tuple(c) for t, c in lab["classes"].items()}
    sizes = {t: len(c) for t, c in classes.items()}
    tok = AutoTokenizer.from_pretrained(a.hf)
    first = json.loads((a.arm_dir / "pred_needs_action.json").read_text(encoding="utf-8"))
    ids = {s: first["splits"][s]["ids"] for s in ("val", "test")}
    by_id = dict(zip(*Q.read_texts(a.scale_texts), strict=True))
    arr = {s: (ids[s], T.build_arrays(ids[s], lab["labels"][s], classes)) for s in ids}
    logits = {}
    for s in ids:
        lg = Q.score_texts(
            a.model, tok, [by_id[i] for i in ids[s]], a.max_len, a.truncation, a.threads
        )
        logits[s] = Q.split_logits(lg, sizes)
    pred_dir = a.out / "pred"
    pred_dir.mkdir(parents=True, exist_ok=True)
    decisions: dict[str, dict] = {}
    temps = T.write_predictions(pred_dir, classes, arr, logits, decisions)
    meta = json.loads((a.arm_dir / "metadata.json").read_text(encoding="utf-8"))
    meta["quantisation"] = {
        "method": "engine.intent.quantize_m1 (static int8, automatic m1 rule)",
        "variant": a.variant,
        "scoring": "batch 1, production numerics",
    }
    meta["temperatures"], meta["decision"] = temps, decisions
    (pred_dir / "metadata.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    doc = build(pred_dir, a.labels, None, a.variant)
    doc["max_len"] = a.max_len
    doc["truncation"] = a.truncation
    (a.out / "thresholds_run4.json").write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    f_ids, f_texts = Q.read_texts(a.fresh16_texts)
    flg = Q.score_texts(a.model, tok, f_texts, a.max_len, a.truncation, a.threads)
    np.savez(a.out / "fresh16_run4.npz", logits=flg, ids=np.array(f_ids))
    print(
        json.dumps(
            {
                "temperatures": {t: round(v, 4) for t, v in temps.items() if v and t in Q.SERVED},
                "fresh16_scored": len(f_ids),
            }
        )
    )


if __name__ == "__main__":
    main()
