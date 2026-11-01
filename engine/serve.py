"""E3 serving: predict(text) -> (label, confidence, is_unknown). CPU only, no LLM, no network.

Artifacts (written by engine/export.py) live in one directory:
  model.int8.onnx   dynamically quantised encoder returning (pooled embedding)
  head.npz          W, b of the linear head
  scorer.json       labels, temperature, unknown threshold, scorer name
  maha.npz          (optional) class means + shared precision for the Mahalanobis scorer
  tokenizer files   Hugging Face fast tokenizer
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.special import logsumexp, softmax

MAX_LEN = 64


@dataclass(frozen=True)
class Prediction:
    label: str
    confidence: float
    is_unknown: bool


class Predictor:
    def __init__(self, artifact_dir: str | Path, threads: int = 1) -> None:
        import onnxruntime as ort
        from transformers import AutoTokenizer

        d = Path(artifact_dir)
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        self.sess = ort.InferenceSession(
            str(d / "model.int8.onnx"), so, providers=["CPUExecutionProvider"]
        )
        self.tok = AutoTokenizer.from_pretrained(str(d))
        head = np.load(d / "head.npz")
        self.w, self.b = head["W"], head["b"]
        meta = json.loads((d / "scorer.json").read_text(encoding="utf-8"))
        self.labels: list[str] = meta["labels"]
        self.t: float = meta["temperature"]
        self.threshold: float = meta["threshold"]
        self.scorer: str = meta["scorer"]
        self._maha = np.load(d / "maha.npz") if (d / "maha.npz").exists() else None

    def _embed(self, texts: list[str]) -> np.ndarray:
        b = self.tok(texts, padding=True, truncation=True, max_length=MAX_LEN, return_tensors="np")
        feeds = {i.name: b[i.name].astype(np.int64) for i in self.sess.get_inputs() if i.name in b}
        return self.sess.run(None, feeds)[0]

    def _known_score(self, logits: np.ndarray, emb: np.ndarray) -> np.ndarray:
        if self.scorer == "energy":
            return self.t * logsumexp(logits / self.t, axis=1)
        if self.scorer == "mahalanobis" and self._maha is not None:
            mu, prec = self._maha["mu"], self._maha["prec"]
            xp = emb @ prec
            m = (xp * emb).sum(1)[:, None] - 2 * xp @ mu.T + ((mu @ prec) * mu).sum(1)[None, :]
            return -m.min(axis=1)
        return softmax(logits / self.t, axis=1).max(axis=1)  # msp_calibrated

    def predict_batch(self, texts: list[str]) -> list[Prediction]:
        emb = self._embed(texts)
        logits = emb @ self.w.T + self.b
        probs = softmax(logits / self.t, axis=1)
        known = self._known_score(logits, emb)
        out = []
        for i in range(len(texts)):
            j = int(probs[i].argmax())
            out.append(
                Prediction(self.labels[j], float(probs[i, j]), bool(known[i] < self.threshold))
            )
        return out

    def predict(self, text: str) -> Prediction:
        return self.predict_batch([text])[0]


def create_app(artifact_dir: str | Path):  # noqa: ANN201 (FastAPI app)
    from fastapi import FastAPI
    from pydantic import BaseModel, Field

    class Req(BaseModel):
        texts: list[str] = Field(min_length=1, max_length=64)

    class Item(BaseModel):
        label: str
        confidence: float
        is_unknown: bool

    app = FastAPI(title="classification-engine")
    pred = Predictor(artifact_dir)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/predict", response_model=list[Item])
    def predict(req: Req) -> list[Item]:
        return [Item(**p.__dict__) for p in pred.predict_batch(req.texts)]

    return app
