"""Amendment 16: quantise a trained checkpoint to the SERVED static-int8 form, with the layer-sensitivity rule of serving spec Addendum 1 run
automatically, and score texts with it on the production numerics.

    PYTHONPATH=. python -m engine.intent.quantize_m1 quantize --weights model.pt --hf <hf dir> --calib-texts calib.jsonl \
        --val-texts val.jsonl --ref-pred-dir <fp32 pred dir> --out <dir> --max-len 128 --truncation head
    PYTHONPATH=. python -m engine.intent.quantize_m1 score --model <dir>/model_m1_auto.onnx --hf <hf dir> --texts items.jsonl --out logits.npz \
        --max-len 128 --truncation head

The rule (registered before any number): quantise every first feed-forward MatMul (static int8, QDQ, per-channel weights, MinMax calibration on 256
TRAINING texts drawn with random.Random(42)); for each encoder layer score its second feed-forward MatMul ALONE (everything else fp32) on the
VALIDATION items as the mean over the four served tasks of argmax agreement with the fp32 model; layers below 0.995 keep that MatMul in fp32. The
model is accepted only if its validation agreement with fp32 is at least 0.90. Everything is scored with `session.x64quantprecision=1`. Review text is
read from private files and never written. Calibration and scoring are one review per forward, as the endpoint does.
"""

from __future__ import annotations

import argparse
import json
import random
import tempfile
import time
from pathlib import Path

import numpy as np

SERVED = ("needs_action", "broad_intent", "sentiment", "urgency")
SAFE_AGREEMENT = 0.995  # Addendum 1: a layer whose single-MatMul quantisation agrees below this keeps that MatMul in fp32
ACCEPT_AGREEMENT = 0.90  # the serving spec's registered acceptance criterion
N_CALIB = 256
SEED = 42
N_LAYERS = 12


def choose_safe_layers(
    agreement: dict[int, float], threshold: float = SAFE_AGREEMENT
) -> tuple[int, ...]:
    """Layers whose second feed-forward MatMul may be quantised (single-layer validation agreement >= threshold)."""
    return tuple(sorted(layer for layer, a in agreement.items() if a >= threshold))


def mean_agreement(logits: dict[str, np.ndarray], ref_argmax: dict[str, np.ndarray]) -> float:
    return float(np.mean([(logits[t].argmax(1) == ref_argmax[t]).mean() for t in SERVED]))


def m1_skip_layers(safe: tuple[int, ...]) -> tuple[int, ...]:
    """Layers whose second feed-forward MatMul stays fp32."""
    return tuple(layer for layer in range(N_LAYERS) if layer not in safe)


def split_logits(lg: np.ndarray, sizes: dict[str, int]) -> dict[str, np.ndarray]:
    out, i = {}, 0
    for t, n in sizes.items():
        out[t] = lg[:, i : i + n]
        i += n
    return out


def read_texts(path: Path) -> tuple[list[str], list[str]]:
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return [r["id"] for r in rows], [r["text"] for r in rows]


def pinned_session(model: Path, threads: int):  # noqa: ANN201
    import onnxruntime as ort
    from app.core.classify.ort_numerics import apply_numerics_pin

    so = apply_numerics_pin(ort.SessionOptions())
    so.intra_op_num_threads = threads
    so.inter_op_num_threads = 1
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(str(model), so, providers=["CPUExecutionProvider"])


def score_texts(
    model: Path, tok, texts: list[str], max_len: int, mode: str, threads: int = 4
) -> np.ndarray:  # noqa: ANN001
    """Raw logits (all head outputs), one review per forward, on the production numerics."""
    from engine.intent import inputs as IN

    sess = pinned_session(model, threads)
    rows = []
    for t in texts:
        b = IN.encode_batch(tok, [t], max_len, mode)
        rows.append(sess.run(["logits"], b)[0].astype(np.float32)[0])
    return np.stack(rows)


def fp32_ref_argmax(pred_dir: Path, ids: list[str]) -> dict[str, np.ndarray]:
    """argmax of the fp32 model's calibrated validation probabilities (its pred files), aligned to `ids`."""
    out = {}
    for t in SERVED:
        d = json.loads((pred_dir / f"pred_{t}.json").read_text(encoding="utf-8"))
        pos = {i: k for k, i in enumerate(d["splits"]["val"]["ids"])}
        p = np.array(d["splits"]["val"]["probs"]).reshape(len(pos), len(d["classes"]))
        out[t] = p[[pos[i] for i in ids]].argmax(1)
    return out


def quantize(a: argparse.Namespace) -> None:
    from scripts import onnx_static_quantize as Q
    from transformers import AutoTokenizer

    out: Path = a.out
    out.mkdir(parents=True, exist_ok=True)
    classes = json.loads(Path(a.labels).read_text(encoding="utf-8"))["classes"]
    n_out = sum(len(c) for c in classes.values())
    sizes = {t: len(classes[t]) for t in SERVED}
    tok = AutoTokenizer.from_pretrained(a.hf)
    fp32, emb = out / "model_fp32.onnx", out / "model_embint8.onnx"
    Q.export_fp32(Q.build_net(a.weights, a.hf, n_out), fp32)
    emb_info = Q.embed_int8(fp32, emb)
    c_ids, c_texts = read_texts(a.calib_texts)
    pick = random.Random(SEED).sample(sorted(c_ids), N_CALIB)
    by_id = dict(zip(c_ids, c_texts, strict=True))
    calib = [by_id[i] for i in pick]
    v_ids, v_texts = read_texts(a.val_texts)
    ref = fp32_ref_argmax(a.ref_pred_dir, v_ids)

    def build(nodes: list[str], dst: Path) -> None:
        Q.quantize(emb, dst, nodes, "minmax", tok, calib, a.max_len, a.truncation)

    agreement: dict[int, float] = {}
    t0 = time.time()
    for layer in range(N_LAYERS):  # each layer's second FFN MatMul ALONE
        nodes = Q.pick_nodes(emb, "ffn_out", tuple(k for k in range(N_LAYERS) if k != layer))
        with tempfile.TemporaryDirectory() as d:
            m = Path(d) / "one.onnx"
            build(nodes, m)
            lg = split_logits(
                score_texts(m, tok, v_texts, a.max_len, a.truncation, a.threads), sizes
            )
        agreement[layer] = round(mean_agreement(lg, ref), 4)
        print("layer", layer, "agreement", agreement[layer], f"{time.time() - t0:.0f}s", flush=True)
    safe = choose_safe_layers(agreement)
    nodes = Q.pick_nodes(emb, "ffn_in") + Q.pick_nodes(emb, "ffn_out", m1_skip_layers(safe))
    dst = out / "model_m1_auto.onnx"
    build(nodes, dst)
    lg = split_logits(score_texts(dst, tok, v_texts, a.max_len, a.truncation, a.threads), sizes)
    val_agreement = round(mean_agreement(lg, ref), 4)
    manifest = {
        "rule": "serving spec Addendum 1, automatic: ffn_in all layers + ffn_out where single-layer validation agreement >= 0.995",
        "max_len": a.max_len, "truncation": a.truncation, "calibration_ids": pick, "n_calib": N_CALIB, "seed": SEED,
        "layer_agreement": agreement, "safe_layers": list(safe), "n_quantized_matmuls": len(nodes),
        "validation_agreement_with_fp32": val_agreement, "accepted": val_agreement >= ACCEPT_AGREEMENT, "accept_threshold": ACCEPT_AGREEMENT,
        "embedding": emb_info, "bytes": dst.stat().st_size, "sha256": Q.sha256(dst), "seconds": round(time.time() - t0),
    }  # fmt: skip
    (out / "m1_manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    for p in (fp32, emb, out / "model_fp32.onnx.data"):
        p.unlink(missing_ok=True)  # intermediate artifacts (the fp32 export is ~1.1 GB)
    print(
        json.dumps(
            {
                k: manifest[k]
                for k in ("safe_layers", "validation_agreement_with_fp32", "accepted", "bytes")
            }
        ),
        flush=True,
    )


def score(a: argparse.Namespace) -> None:
    from transformers import AutoTokenizer

    ids, texts = read_texts(a.texts)
    tok = AutoTokenizer.from_pretrained(a.hf)
    lg = score_texts(a.model, tok, texts, a.max_len, a.truncation, a.threads)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(a.out, logits=lg, ids=np.array(ids))
    print("scored", len(ids), "texts ->", a.out)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("quantize")
    q.add_argument("--weights", type=Path, required=True)
    q.add_argument("--hf", type=Path, required=True)
    q.add_argument("--labels", type=Path, default=Path("reports/intent/labels/labels.json"))
    q.add_argument(
        "--calib-texts",
        type=Path,
        required=True,
        help="jsonl {id,text} of the TRAINING pool to sample 256 from",
    )
    q.add_argument(
        "--val-texts",
        type=Path,
        required=True,
        help="jsonl {id,text} of the original validation items",
    )
    q.add_argument("--ref-pred-dir", type=Path, required=True)
    q.add_argument("--out", type=Path, required=True)
    q.set_defaults(fn=quantize)
    s = sub.add_parser("score")
    s.add_argument("--model", type=Path, required=True)
    s.add_argument("--hf", type=Path, required=True)
    s.add_argument("--texts", type=Path, required=True)
    s.add_argument("--out", type=Path, required=True)
    s.set_defaults(fn=score)
    for p in (q, s):
        p.add_argument("--max-len", type=int, default=128)
        p.add_argument("--truncation", default="head", choices=["head", "head_tail"])
        p.add_argument("--threads", type=int, default=4)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
