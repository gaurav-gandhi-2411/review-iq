"""Build the static-int8 ONNX serving candidates for the review-intent encoder (docs/specs/review-intent-serving.md).

    python scripts/onnx_static_quantize.py --weights D:/ml-cache/s23/quant/fp32_cpu.pt \
        --hf D:/ml-cache/s24/classify_artifacts/hf --texts D:/ml-cache/review-intent/scale.jsonl \
        --out D:/ml-cache/s25/onnx

Build-time tool (needs torch, transformers, onnx, onnxruntime); the serving image needs none of torch/transformers/onnx.
Steps: fp32 checkpoint -> ONNX (opset 17, mean pooling and the head inside the graph) -> word-embedding table stored as int8 with one
scale per row (a Gather on the int8 table, then a per-row rescale, so the 250k x 768 table is never expanded) -> `quantize_static`
(QDQ, int8 per-channel weights, uint8 activations) of the selected MatMuls, calibrated on TRAINING texts only, one review per forward.
The three candidates of the spec: s1_ffn_minmax, s2_ffn_percentile, s3_all_minmax. Review text is read from the private scale.jsonl
and is never written; the manifest holds ids and hashes only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

import numpy as np

MAX_LEN = 128  # training max_len (reports/intent/run2_cpu/*/metadata.json)
N_CALIB = 256
SEED = 42
VARIANTS = {
    "s1_ffn_minmax": {"scope": "ffn", "method": "minmax"},
    "s2_ffn_percentile": {"scope": "ffn", "method": "percentile"},
    "s3_all_minmax": {"scope": "all", "method": "minmax"},
    # post-hoc diagnostics, added after s1-s3 all missed the 0.90 validation threshold (spec: Addendum 1); chosen on validation only
    "d1_ffn_out_minmax": {"scope": "ffn_out", "method": "minmax"},
    "d2_ffn_in_minmax": {"scope": "ffn_in", "method": "minmax"},
    # Addendum 1 post-hoc rule: second FFN MatMul quantised only in layers whose single-layer validation agreement was >= 0.995
    "m1_ffn_in_plus_safe_out": {
        "scope": "ffn_in",
        "method": "minmax",
        "plus": {"scope": "ffn_out", "skip_layers": [2, 3, 4, 5, 6, 7, 8]},
    },
}


def build_net(weights: Path, hf: Path, n_out: int):  # noqa: ANN201 (torch is imported lazily)
    import torch
    from torch import nn
    from transformers import AutoConfig, AutoModel

    class Net(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.enc = AutoModel.from_config(AutoConfig.from_pretrained(hf))
            self.head = nn.Linear(self.enc.config.hidden_size, n_out)

        def forward(self, input_ids, attention_mask):  # noqa: ANN001, ANN201
            h = self.enc(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state
            m = attention_mask.unsqueeze(-1).to(h.dtype)
            return self.head((h * m).sum(1) / m.sum(1).clamp(min=1))

    net = Net().float().eval()
    net.load_state_dict(torch.load(weights, map_location="cpu"))
    return net


def export_fp32(net, path: Path) -> None:  # noqa: ANN001
    import torch

    ids = torch.ones(2, 8, dtype=torch.long)
    torch.onnx.export(
        net, (ids, torch.ones_like(ids)), str(path),
        input_names=["input_ids", "attention_mask"], output_names=["logits"],
        dynamic_axes={"input_ids": {0: "b", 1: "s"}, "attention_mask": {0: "b", 1: "s"}, "logits": {0: "b"}},
        opset_version=17, dynamo=False,
    )  # fmt: skip


def embed_int8(src: Path, dst: Path) -> dict:
    """Replace the word-embedding initializer by int8 + per-row scale; Gather on the int8 table, then rescale per row."""
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    m = onnx.load(str(src), load_external_data=True)
    inits = {i.name: i for i in m.graph.initializer}
    gather = next(
        n for n in m.graph.node
        if n.op_type == "Gather" and n.input[0] in inits and len(inits[n.input[0]].dims) == 2 and inits[n.input[0]].dims[0] > 100000
    )  # fmt: skip
    table = numpy_helper.to_array(inits[gather.input[0]]).astype(np.float32)
    scale = np.maximum(np.abs(table).max(axis=1), 1e-12) / 127.0
    q = np.clip(np.rint(table / scale[:, None]), -127, 127).astype(np.int8)
    name = gather.input[0]
    m.graph.initializer.remove(inits[name])
    m.graph.initializer.extend(
        [
            numpy_helper.from_array(q, name + "_q"),
            numpy_helper.from_array(scale.astype(np.float32), name + "_scale"),
        ]
    )
    ids, out = gather.input[1], gather.output[0]
    axes = numpy_helper.from_array(np.array([-1], dtype=np.int64), name + "_ax")
    m.graph.initializer.append(axes)
    new = [
        helper.make_node("Gather", [name + "_q", ids], [name + "_gq"]),
        helper.make_node("Gather", [name + "_scale", ids], [name + "_gs"]),
        helper.make_node("Cast", [name + "_gq"], [name + "_gf"], to=TensorProto.FLOAT),
        helper.make_node("Unsqueeze", [name + "_gs", name + "_ax"], [name + "_gsu"]),
        helper.make_node("Mul", [name + "_gf", name + "_gsu"], [out]),
    ]
    pos = list(m.graph.node).index(gather)
    m.graph.node.remove(gather)
    for k, n in enumerate(new):
        m.graph.node.insert(pos + k, n)
    onnx.save(m, str(dst))
    return {
        "table_shape": list(table.shape),
        "max_abs_err": float(np.abs(q * scale[:, None] - table).max()),
    }


def pick_nodes(model_path: Path, scope: str, skip_layers: tuple[int, ...] = ()) -> list[str]:
    """MatMul nodes whose second input is a weight (Linear layers); `ffn` keeps the feed-forward ones (`ffn_in` / `ffn_out`: only
    the first / second of the two), `all` adds attention projections; `skip_layers` leaves whole encoder layers in fp32."""
    import onnx

    m = onnx.load(str(model_path))
    weights = {i.name for i in m.graph.initializer}
    out = []
    for n in m.graph.node:
        if n.op_type != "MatMul" or n.input[1] not in weights:
            continue
        low = n.name.lower()
        if "head" in low:
            continue  # the 14-output head stays fp32, as in every dynamic variant
        ffn_in, ffn_out = "intermediate" in low, "attention" not in low and "output" in low
        keep = {"all": True, "ffn": ffn_in or ffn_out, "ffn_in": ffn_in, "ffn_out": ffn_out}[scope]
        layer = int(low.split("layer.")[1].split("/")[0]) if "layer." in low else -1
        if keep and layer not in skip_layers:
            out.append(n.name)
    return out


def calib_texts(texts_path: Path, labels: Path) -> list[str]:
    from engine.intent.labels import load_texts

    lab = json.loads(labels.read_text(encoding="utf-8"))["labels"]["train"]
    train_ids = sorted(set().union(*[set(v) for v in lab.values()]))
    pick = random.Random(SEED).sample(train_ids, N_CALIB)
    by_id = load_texts(texts_path)
    return pick, [by_id[i] for i in pick]  # type: ignore[return-value]


class Reader:
    """One review per forward. `tile` repeats the token ids cyclically to exactly MAX_LEN (no padding): ONNX Runtime's percentile
    calibrator stacks the collected activations and needs one shape for every sample, and padding would put pad-position
    activations (never used downstream) into the histograms."""

    def __init__(
        self,
        tok,
        texts: list[str],
        tile: bool = False,
        max_len: int = MAX_LEN,
        mode: str = "head",  # noqa: ANN001
    ) -> None:
        self.it = iter(texts)
        self.tok = tok
        self.tile = tile
        self.max_len = max_len  # Amendment 16: the arm's input length and truncation mode
        self.mode = mode

    def get_next(self) -> dict | None:
        t = next(self.it, None)
        if t is None:
            return None
        if self.mode == "head":  # unchanged path: the S25 calibration
            e = self.tok(t, truncation=True, max_length=self.max_len, return_tensors="np")
            ids = e["input_ids"].astype(np.int64)
        else:
            from engine.intent import inputs as IN

            ids = IN.encode_batch(self.tok, [t], self.max_len, self.mode)["input_ids"]
        if self.tile:
            ids = np.tile(ids, (1, -(-self.max_len // ids.shape[1])))[:, : self.max_len]
        return {"input_ids": ids, "attention_mask": np.ones_like(ids)}


def quantize(
    src: Path, dst: Path, nodes: list[str], method: str, tok, texts: list[str],  # noqa: ANN001
    max_len: int = MAX_LEN, mode: str = "head",
) -> None:  # fmt: skip
    from onnxruntime.quantization import CalibrationMethod, QuantFormat, QuantType, quantize_static

    cm = CalibrationMethod.Percentile if method == "percentile" else CalibrationMethod.MinMax
    extra = {"ActivationSymmetric": False, "WeightSymmetric": True}
    if method == "percentile":
        extra["CalibPercentile"] = 99.999
    quantize_static(
        str(src), str(dst), Reader(tok, texts, method == "percentile", max_len, mode), quant_format=QuantFormat.QDQ, per_channel=True,
        weight_type=QuantType.QInt8, activation_type=QuantType.QUInt8, op_types_to_quantize=["MatMul"],
        nodes_to_quantize=nodes, calibrate_method=cm, extra_options=extra,
    )  # fmt: skip


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", type=Path, required=True)
    ap.add_argument("--hf", type=Path, required=True)
    ap.add_argument("--texts", type=Path, required=True)
    ap.add_argument("--labels", type=Path, default=Path("reports/intent/labels/labels.json"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--variants", default=",".join(VARIANTS))
    a = ap.parse_args()
    from tokenizers import Tokenizer
    from transformers import AutoTokenizer

    a.out.mkdir(parents=True, exist_ok=True)
    lab = json.loads(a.labels.read_text(encoding="utf-8"))["classes"]
    n_out = sum(len(c) for c in lab.values())
    fp32 = a.out / "model_fp32.onnx"
    if not fp32.exists():
        export_fp32(build_net(a.weights, a.hf, n_out), fp32)
    emb = a.out / "model_embint8.onnx"
    info = embed_int8(fp32, emb) if not emb.exists() else {}
    tok = AutoTokenizer.from_pretrained(a.hf)
    Tokenizer.from_file(str(a.hf / "tokenizer.json"))  # the serving image reads exactly this file
    ids, texts = calib_texts(a.texts, a.labels)
    manifest = {
        "calibration_ids": ids,
        "n_calib": N_CALIB,
        "seed": SEED,
        "max_len": MAX_LEN,
        "embedding": info,
        "variants": {},
    }
    for name in a.variants.split(","):
        cfg = VARIANTS[name]
        nodes = pick_nodes(emb, cfg["scope"], tuple(cfg.get("skip_layers", ())))
        if "plus" in cfg:
            nodes += pick_nodes(emb, cfg["plus"]["scope"], tuple(cfg["plus"]["skip_layers"]))
        dst = a.out / f"model_{name}.onnx"
        quantize(emb, dst, nodes, cfg["method"], tok, texts)
        manifest["variants"][name] = {
            **cfg,
            "n_quantized_matmuls": len(nodes),
            "bytes": dst.stat().st_size,
            "sha256": sha256(dst),
        }
        print(name, len(nodes), "matmuls", round(dst.stat().st_size / 1e6, 1), "MB", flush=True)
    (a.out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
