"""Write `pred_<task>.json` (calibrated probabilities, validation-fitted temperature and rare-class thresholds) for an ONNX candidate.

    python scripts/onnx_pred_files.py --logits D:/ml-cache/s25/onnx/logits_m1_valtest.npz \
        --out reports/intent/run2_cpu/onnx_static_int8 --model-sha256 <sha> --variant m1_ffn_in_plus_safe_out

Same writer as every other variant (`engine.intent.train.write_predictions`, as in `engine.intent.quantize`): the temperature is fit
on VALIDATION only; the logits are the batch-1 ones (the way the endpoint scores). `metadata.json` is the run-2 training metadata
plus the quantisation facts, so `engine.intent.evaluate` can read the directory like any run directory.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from engine.intent import train as T

LABELS = Path("reports/intent/labels/labels.json")
FP32 = Path("reports/intent/run2_cpu/fp32_cpu")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logits", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--variant", required=True)
    ap.add_argument("--model-sha256", required=True)
    a = ap.parse_args()
    lab = json.loads(LABELS.read_text(encoding="utf-8"))
    classes = {t: tuple(c) for t, c in lab["classes"].items()}
    z = np.load(a.logits)
    first = json.loads(sorted(FP32.glob("pred_*.json"))[0].read_text(encoding="utf-8"))
    ids = {s: first["splits"][s]["ids"] for s in ("val", "test")}
    arr = {s: (ids[s], T.build_arrays(ids[s], lab["labels"][s], classes)) for s in ids}
    logits = {s: {t: z[f"{s}/b1/{t}"] for t in classes} for s in ids}
    a.out.mkdir(parents=True, exist_ok=True)
    decisions: dict[str, dict] = {}
    temps = T.write_predictions(a.out, classes, arr, logits, decisions)
    meta = json.loads((FP32.parent / "int8_ffn_full" / "metadata.json").read_text(encoding="utf-8"))
    meta["quantisation"] = {
        "method": "onnxruntime quantize_static (QDQ), docs/specs/review-intent-serving.md", "variant": a.variant,
        "model_sha256": a.model_sha256, "scoring": "batch 1 (one review per forward)",
    }  # fmt: skip
    meta["temperatures"], meta["decision"] = temps, decisions
    (a.out / "metadata.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    print(
        "wrote",
        len(list(a.out.glob("pred_*.json"))),
        "pred files; temperatures:",
        {t: round(v, 4) for t, v in temps.items() if v},
    )


if __name__ == "__main__":
    main()
