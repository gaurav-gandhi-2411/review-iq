"""Score validation and test items with an ONNX candidate (batch 1 and batches of 128) and compare with the fp32 torch reference.

    python scripts/onnx_eval.py --model D:/ml-cache/s25/onnx/model_s1_ffn_minmax.onnx --hf D:/ml-cache/s24/classify_artifacts/hf \
        --texts D:/ml-cache/review-intent/scale.jsonl --out D:/ml-cache/s25/onnx/logits_s1_ffn_minmax.npz

Writes the logits (task -> [n, classes]) for the pred-file `val` and `test` id lists in both modes, and prints the validation argmax
agreement with `reports/intent/run2_cpu/fp32_cpu` (the fp32 torch model, pred files carry its calibrated probabilities). Validation
only is used to choose between candidates (spec); the test logits are written for the chosen candidate's later, single use.
Review text is read from the private scale.jsonl and never written.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

PRED = Path("reports/intent/run2_cpu/fp32_cpu")
LABELS = Path("reports/intent/labels/labels.json")
SERVED = ("needs_action", "broad_intent", "sentiment", "urgency")
MAX_LEN = 128


def sizes() -> dict[str, int]:
    return {t: len(c) for t, c in json.loads(LABELS.read_text(encoding="utf-8"))["classes"].items()}


def make_session(path: Path, threads: int = 4):  # noqa: ANN201
    import onnxruntime as ort

    so = ort.SessionOptions()
    so.intra_op_num_threads = threads
    so.inter_op_num_threads = 1
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])


def split(lg: np.ndarray) -> dict[str, np.ndarray]:
    out, i = {}, 0
    for t, n in sizes().items():
        out[t] = lg[:, i : i + n]
        i += n
    return out


def run_batches(sess, tok, texts: list[str], batch: int) -> dict[str, np.ndarray]:  # noqa: ANN001
    parts = []
    for k in range(0, len(texts), batch):
        e = tok(texts[k : k + batch], padding=True, truncation=True, max_length=MAX_LEN, return_tensors="np")
        feed = {"input_ids": e["input_ids"].astype(np.int64), "attention_mask": e["attention_mask"].astype(np.int64)}
        parts.append(sess.run(["logits"], feed)[0].astype(np.float32))
    return split(np.concatenate(parts))


def pred_ids() -> dict[str, list[str]]:
    d = json.loads((PRED / f"pred_{SERVED[0]}.json").read_text(encoding="utf-8"))
    return {s: d["splits"][s]["ids"] for s in ("val", "test")}


def ref_probs(split_name: str, task: str) -> np.ndarray:
    d = json.loads((PRED / f"pred_{task}.json").read_text(encoding="utf-8"))
    n = len(d["classes"])
    return np.array(d["splits"][split_name]["probs"]).reshape(-1, n)


def main() -> None:
    from engine.intent.labels import load_texts
    from transformers import AutoTokenizer

    ap = argparse.ArgumentParser()
    ap.add_argument("--model", type=Path, required=True)
    ap.add_argument("--hf", type=Path, required=True)
    ap.add_argument("--texts", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--splits", default="val,test")
    ap.add_argument("--threads", type=int, default=4)
    a = ap.parse_args()
    tok, sess = AutoTokenizer.from_pretrained(a.hf), make_session(a.model, a.threads)
    by_id, ids = load_texts(a.texts), pred_ids()
    arrays: dict[str, np.ndarray] = {}
    for s in a.splits.split(","):
        texts = [by_id[i] for i in ids[s]]
        for mode, batch in (("b1", 1), ("b128", 128)):
            lg = run_batches(sess, tok, texts, batch)
            for t, v in lg.items():
                arrays[f"{s}/{mode}/{t}"] = v
        agree = {t: float((arrays[f"{s}/b1/{t}"].argmax(1) == ref_probs(s, t).argmax(1)).mean()) for t in SERVED}
        flips = {t: int((arrays[f"{s}/b1/{t}"].argmax(1) != arrays[f"{s}/b128/{t}"].argmax(1)).sum()) for t in SERVED}
        print(s, "argmax agreement with fp32 torch (batch 1):", {t: round(v, 4) for t, v in agree.items()},
              "mean", round(float(np.mean(list(agree.values()))), 4), "| argmax flips b1 vs b128:", flips, flush=True)  # fmt: skip
    np.savez(a.out, **arrays)


if __name__ == "__main__":
    main()
