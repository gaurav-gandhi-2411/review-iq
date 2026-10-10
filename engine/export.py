"""Export a trained Classifier to the serving artifact directory (see engine/serve.py)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import torch
from torch import nn

from engine.train import MAX_LEN, Classifier


class _Embed(nn.Module):
    def __init__(self, clf: Classifier) -> None:
        super().__init__()
        self.enc = clf.enc

    def forward(self, input_ids: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
        h = self.enc(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state
        m = attention_mask.unsqueeze(-1).to(h.dtype)
        return (h * m).sum(1) / m.sum(1).clamp(min=1)


def export(
    clf: Classifier,
    tok,
    out_dir: str | Path,
    labels: list[str],
    temperature: float,
    threshold: float,
    scorer: str,
    maha=None,
) -> Path:
    from onnxruntime.quantization import QuantType, quantize_dynamic

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    clf = clf.cpu().float().eval()
    wrapper = _Embed(clf).eval()
    ids = torch.ones(2, 8, dtype=torch.long)
    names = [n for n in ("input_ids", "attention_mask")]
    fp32 = out / "model.fp32.onnx"
    torch.onnx.export(
        wrapper, (ids, torch.ones_like(ids)), str(fp32), input_names=names, output_names=["emb"],
        dynamic_axes={"input_ids": {0: "b", 1: "s"}, "attention_mask": {0: "b", 1: "s"}, "emb": {0: "b"}},
        opset_version=17, dynamo=False,
    )  # fmt: skip
    quantize_dynamic(str(fp32), str(out / "model.int8.onnx"), weight_type=QuantType.QInt8)
    fp32.unlink()
    np.savez(out / "head.npz", W=clf.head.weight.detach().numpy(), b=clf.head.bias.detach().numpy())
    (out / "scorer.json").write_text(
        json.dumps(
            {"labels": labels, "temperature": temperature, "threshold": threshold, "scorer": scorer,
             "max_len": MAX_LEN},
            indent=1,
        ),
        encoding="utf-8",
    )  # fmt: skip
    if maha is not None:
        np.savez(out / "maha.npz", mu=maha.mu, prec=maha.prec)
    tok.save_pretrained(str(out))
    return out
