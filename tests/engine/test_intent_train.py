from __future__ import annotations

import json
import zlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

for _m in (
    "torch",
    "transformers",
    "sklearn",
    "scipy",
):  # absent from the CI venv: skip like test_engine_core
    pytest.importorskip(_m)

import torch  # noqa: E402
from engine.intent import train as T  # noqa: E402
from engine.intent.model import MultiHeadClassifier  # noqa: E402
from torch import nn  # noqa: E402

CLASSES = {"needs_action": ("yes", "no"), "sentiment": ("positive", "negative"), "aspect:food:taste": ("positive", "negative", "neutral")}  # fmt: skip


class StubEncoder(nn.Module):
    """Embedding bag standing in for e5: offline, ~1k parameters, same call signature."""

    def __init__(self) -> None:
        super().__init__()
        self.config = SimpleNamespace(hidden_size=16)
        self.emb = nn.Embedding(512, 16)

    def get_input_embeddings(self) -> nn.Embedding:
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


def stub_loader(name: str, sizes: dict[str, int]) -> tuple[MultiHeadClassifier, object]:
    torch.manual_seed(0)
    return MultiHeadClassifier(StubEncoder(), sizes), stub_tok


def synthetic(n: int = 60) -> tuple[dict, dict[str, str]]:
    """Label is a pure function of the words, so a working model must beat chance."""
    texts, labels, ids = {}, {s: {t: {} for t in CLASSES} for s in ("train", "val", "test")}, {}
    for s, k in (("train", n), ("val", 24), ("test", 24)):
        ids[s] = []
        for j in range(k):
            i = f"{s}{j}"
            bad = j % 2 == 0
            texts[i] = f"item {j % 5} " + ("broken awful" if bad else "lovely great")
            ids[s].append(i)
            labels[s]["needs_action"][i] = "yes" if bad else "no"
            labels[s]["sentiment"][i] = "negative" if bad else "positive"
            if j % 4 == 0:  # aspect labelled on a quarter of items only (masked elsewhere)
                labels[s]["aspect:food:taste"][i] = "negative" if bad else "positive"
    return {"classes": CLASSES, "labels": labels, "ids": ids}, texts


def test_masked_loss_ignores_missing_labels_and_empty_tasks() -> None:
    lg = {"a": torch.tensor([[2.0, 0.0], [0.0, 2.0]]), "b": torch.zeros(2, 3)}
    y_all = {"a": torch.tensor([0, 1]), "b": torch.tensor([T.IGNORE, T.IGNORE])}
    only_a = T.masked_loss(lg, y_all)
    assert torch.isfinite(only_a)  # task b has no label: not NaN
    assert torch.isclose(only_a, nn.functional.cross_entropy(lg["a"], y_all["a"]))
    y_half = {"a": torch.tensor([0, T.IGNORE]), "b": torch.tensor([T.IGNORE, T.IGNORE])}
    assert torch.isclose(
        T.masked_loss(lg, y_half), nn.functional.cross_entropy(lg["a"][:1], y_half["a"][:1])
    )  # noqa: E501


def test_head_slices_match_task_sizes() -> None:
    model, _ = stub_loader("x", {"a": 2, "b": 3})
    out = model.split(torch.arange(10.0).reshape(2, 5))
    assert out["a"].shape == (2, 2) and out["b"].shape == (2, 3)
    assert out["b"][0].tolist() == [2.0, 3.0, 4.0]


def test_run_end_to_end_with_stub_encoder(tmp_path: Path) -> None:
    data, texts = synthetic()
    cfg = T.IntentConfig(model="stub", epochs=8, lr=5e-2, batch_size=16, max_len=8)
    meta = T.run(cfg, data, texts, tmp_path, "cpu", loader=stub_loader)
    assert meta["config"]["seed"] == 42 and "commit" in meta
    assert meta["train_info"]["best_val_mean_macro_f1"] > 0.9
    p = json.loads((tmp_path / "pred_aspect__food__taste.json").read_text(encoding="utf-8"))
    assert p["task"] == "aspect:food:taste" and p["classes"] == ["positive", "negative", "neutral"]
    assert p["splits"]["test"]["ids"] == data["ids"]["test"]  # predictions for EVERY test item
    probs = np.array(p["splits"]["test"]["probs"])
    assert probs.shape == (24, 3) and np.allclose(probs.sum(1), 1, atol=1e-4)
    q = json.loads((tmp_path / "pred_needs_action.json").read_text(encoding="utf-8"))
    pred = np.array(q["splits"]["test"]["probs"]).argmax(1)
    gold = np.array([0 if i % 2 == 0 else 1 for i in range(24)])
    assert (pred == gold).mean() > 0.9
    assert meta["n_labelled"]["val"]["aspect:food:taste"] == 6


def test_deterministic_and_temperature_none_without_val_labels(tmp_path: Path) -> None:
    data, texts = synthetic(32)
    data["labels"]["val"]["aspect:food:taste"] = {}  # no validation labels for this task
    cfg = T.IntentConfig(model="stub", epochs=2, lr=5e-2, batch_size=16, max_len=8)
    a = T.run(cfg, data, texts, tmp_path / "a", "cpu", loader=stub_loader)
    T.run(cfg, data, texts, tmp_path / "b", "cpu", loader=stub_loader)
    assert a["temperatures"]["aspect:food:taste"] is None
    assert a["temperatures"]["needs_action"] is not None
    f = "pred_needs_action.json"
    assert (tmp_path / "a" / f).read_text() == (tmp_path / "b" / f).read_text()
