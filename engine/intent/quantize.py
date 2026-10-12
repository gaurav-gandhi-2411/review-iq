"""CPU int8 export of the multi-task review-intent model and its probabilities (S23).

    python -m engine.intent.quantize --weights D:/ml-cache/s23/run2/model.pt --pred-dir reports/intent/run2 \
        --labels reports/intent/labels/labels.json --texts D:/ml-cache/review-intent/scale.jsonl \
        --out-dir reports/intent/run2_cpu --scratch D:/ml-cache/s23/quant

Method: `torch.ao.quantization.quantize_dynamic`, chosen over ONNX Runtime int8 because it needs no extra package, no
export step and no calibration set (weights are quantised once, activations on the fly), so it reproduces from the
checkpoint alone. Five CPU variants are scored on the SAME validation and sealed items as the GPU run:
  fp32_cpu      the checkpoint in fp32 on CPU (separates "CPU vs GPU fp16" noise from quantisation error)
  int8_linear   every nn.Linear in the encoder and heads -> qint8 (dynamic); the 250k-row word-embedding table stays fp32
  int8_perchannel / int8_perchannel_full   the same two, but with one scale per output channel of each Linear
  int8_ffn_full  per-channel int8 on the feed-forward Linears + int8 embedding table; attention and heads stay fp32
  int8_full     as int8_linear, plus the word-embedding table -> uint8 weight-only (per-row scale and zero point)
For each variant the logits are recomputed, a per-task temperature is REFIT on validation only, and pred_<task>.json
is written in the format `engine.intent.evaluate` reads (so thresholds are re-picked on validation by the evaluator).
Writes metadata.json with file sizes and the argmax agreement with the GPU predictions. No review text is written.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.ao.quantization import float_qparams_weight_only_qconfig, quantize_dynamic

from engine.intent import train as T
from engine.intent.labels import load_texts
from engine.intent.model import E5, MultiHeadClassifier, load_pretrained

VARIANTS = (
    "fp32_cpu",
    "int8_linear",
    "int8_full",
    "int8_perchannel",
    "int8_perchannel_full",
    "int8_ffn_full",
)


def quantize(model: MultiHeadClassifier, variant: str) -> nn.Module:
    """Return a CPU copy of `model` for the variant (the input model is not modified)."""
    m = copy.deepcopy(model).float().cpu().eval()
    if variant == "fp32_cpu":
        return m
    if variant == "int8_ffn_full":
        # mixed precision: per-channel int8 on the feed-forward Linears and the embedding table only; attention
        # projections and the heads stay fp32 (tests whether attention is where the quantisation error comes from)
        word = m.enc.get_input_embeddings()
        spec = {
            n: torch.ao.quantization.per_channel_dynamic_qconfig
            for n, mod in m.named_modules()
            if isinstance(mod, nn.Linear)
            and "attention" not in n
            and n.startswith("enc.")
            and ("intermediate" in n or n.endswith("output.dense"))
        }
        spec[next(n for n, mod in m.named_modules() if mod is word)] = (
            float_qparams_weight_only_qconfig
        )
        return quantize_dynamic(m, qconfig_spec=spec, dtype=torch.qint8)
    per_channel = variant.startswith("int8_perchannel")
    spec: dict = {
        nn.Linear: torch.ao.quantization.per_channel_dynamic_qconfig
        if per_channel
        else torch.ao.quantization.default_dynamic_qconfig
    }
    if variant in ("int8_full", "int8_perchannel_full"):
        # only the word-embedding table: the position-id lookup is a non-contiguous slice, which the
        # quantised embedding op rejects, and those tables are small anyway
        word = m.enc.get_input_embeddings()
        name = next(n for n, mod in m.named_modules() if mod is word)
        spec[name] = float_qparams_weight_only_qconfig
    elif variant not in ("int8_linear", "int8_perchannel"):
        raise ValueError(f"unknown variant {variant}")
    return quantize_dynamic(m, qconfig_spec=spec, dtype=torch.qint8)


def file_mb(model: nn.Module, path: Path) -> float:
    torch.save(model.state_dict(), path)
    return round(os.path.getsize(path) / 1e6, 1)


def load_checkpoint(weights: Path, sizes: dict[str, int]) -> tuple[MultiHeadClassifier, object]:
    model, tok = load_pretrained(E5, sizes)
    model.load_state_dict(torch.load(weights, map_location="cpu"))
    return model.float().eval(), tok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", required=True)
    ap.add_argument(
        "--pred-dir", required=True, help="the GPU run's pred_*.json (ids order + fp16 reference)"
    )
    ap.add_argument("--labels", required=True)
    ap.add_argument("--texts", required=True)
    ap.add_argument("--out-dir", required=True, help="one sub-directory per variant")
    ap.add_argument(
        "--scratch", required=True, help="where the model files are written to measure their size"
    )
    ap.add_argument("--threads", type=int, default=0)
    a = ap.parse_args()
    if a.threads:
        torch.set_num_threads(a.threads)
    lab = json.loads(Path(a.labels).read_text(encoding="utf-8"))
    classes = {t: tuple(c) for t, c in lab["classes"].items()}
    pdir = Path(a.pred_dir)
    ref = {}
    for f in sorted(pdir.glob("pred_*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        ref[d["task"]] = d
    first = next(iter(ref.values()))
    ids = {s: first["splits"][s]["ids"] for s in ("val", "test")}
    arr = {s: (ids[s], T.build_arrays(ids[s], lab["labels"][s], classes)) for s in ids}
    texts = load_texts(Path(a.texts))
    txt = {s: [texts[i] for i in ids[s]] for s in ids}
    cfg = json.loads((pdir / "metadata.json").read_text(encoding="utf-8"))["config"]
    cfg = T.IntentConfig(**cfg)
    base, tok = load_checkpoint(Path(a.weights), {t: len(c) for t, c in classes.items()})
    scratch = Path(a.scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    meta: dict = {"torch": torch.__version__, "threads": torch.get_num_threads(), "variants": {}}
    for v in VARIANTS:
        t0 = time.time()
        model = quantize(base, v)
        logits = {s: T.infer(model, tok, txt[s], cfg, "cpu") for s in ids}
        out = Path(a.out_dir) / v
        out.mkdir(parents=True, exist_ok=True)
        decisions: dict[str, dict] = {}  # rare-class thresholds refit on validation for THIS variant
        temps = T.write_predictions(out, classes, arr, logits, decisions)
        agree = {
            s: {
                t: round(float((logits[s][t].argmax(1) == np.array(ref[t]["splits"][s]["probs"]).argmax(1)).mean()), 4)
                for t in classes
            }
            for s in ids
        }  # fmt: skip
        meta["variants"][v] = {
            "model_file_mb": file_mb(model, scratch / f"{v}.pt"), "temperatures": temps,
            "decision": decisions, "argmax_agreement_with_gpu_fp16": agree, "seconds": round(time.time() - t0, 1),
        }  # fmt: skip
        print(
            v,
            meta["variants"][v]["model_file_mb"],
            "MB",
            meta["variants"][v]["seconds"],
            "s",
            flush=True,
        )
    meta["fp32_checkpoint_mb"] = round(os.path.getsize(a.weights) / 1e6, 1)
    (Path(a.out_dir) / "metadata.json").write_text(
        json.dumps(meta, indent=1) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
