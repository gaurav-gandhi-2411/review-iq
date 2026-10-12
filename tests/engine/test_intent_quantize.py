from __future__ import annotations

import zlib
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("torch")
pytest.importorskip("transformers")

import torch  # noqa: E402
from engine.intent import quantize as Q  # noqa: E402
from engine.intent import train as T  # noqa: E402
from engine.intent.model import MultiHeadClassifier  # noqa: E402


class StubEncoder(torch.nn.Module):
    """Embedding bag standing in for e5 (same call signature, ~8k parameters)."""

    def __init__(self) -> None:
        super().__init__()
        self.config = SimpleNamespace(hidden_size=16)
        self.emb = torch.nn.Embedding(512, 16)

    def get_input_embeddings(self) -> torch.nn.Embedding:
        return self.emb

    def forward(self, input_ids: torch.Tensor, attention_mask: torch.Tensor) -> SimpleNamespace:
        return SimpleNamespace(last_hidden_state=self.emb(input_ids))


def stub_tok(
    texts: list[str], padding: bool, truncation: bool, max_length: int, return_tensors: str
):  # noqa: ANN201
    rows = [[zlib.crc32(w.encode()) % 511 + 1 for w in t.split()][:max_length] for t in texts]
    n = max(len(r) for r in rows)
    ids = torch.tensor([r + [0] * (n - len(r)) for r in rows])
    return {"input_ids": ids, "attention_mask": (ids > 0).long()}


def make() -> MultiHeadClassifier:
    torch.manual_seed(0)
    enc = StubEncoder()
    m = MultiHeadClassifier(enc, {"a": 2, "b": 3})
    m.head = torch.nn.Linear(16, 5)
    return m.eval()


def logits(model: torch.nn.Module) -> np.ndarray:
    cfg = T.IntentConfig(model="stub", max_len=8)
    out = T.infer(
        model, stub_tok, ["broken awful item", "lovely great", "item 3 lovely"], cfg, "cpu"
    )
    return np.concatenate([out["a"], out["b"]], axis=1)


@pytest.mark.parametrize(
    "variant",
    [
        "fp32_cpu",
        "int8_linear",
        "int8_full",
        "int8_perchannel",
        "int8_perchannel_full",
        "int8_ffn_full",
    ],
)
def test_variants_run_and_stay_close_to_fp32(variant: str) -> None:
    base = make()
    ref = logits(base)
    q = Q.quantize(base, variant)
    assert np.allclose(logits(base), ref)  # the source model is untouched
    got = logits(q)
    assert got.shape == ref.shape and np.isfinite(got).all()
    assert np.abs(got - ref).max() < (1e-5 if variant == "fp32_cpu" else 0.25)


def test_int8_changes_module_types_and_unknown_variant_fails_loudly() -> None:
    base = make()
    lin = Q.quantize(base, "int8_linear")
    assert type(lin.head).__module__.endswith("quantized.dynamic.modules.linear")
    assert (
        type(lin.enc.get_input_embeddings()) is torch.nn.Embedding
    )  # table kept fp32 in this variant
    full = Q.quantize(base, "int8_full")
    assert "quantized" in type(full.enc.get_input_embeddings()).__module__
    with pytest.raises(ValueError):
        Q.quantize(base, "int4")
