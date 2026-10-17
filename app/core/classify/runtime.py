"""Model runtime and decisioning for POST /v2/classify.

Served model: run 2, `int8_ffn_full` (torch dynamic int8 on the encoder feed-forward Linears + int8 word-embedding table;
attention and heads stay fp32), exactly the variant `engine.intent.quantize` exports. The int8 state dict is loaded into a
module built the same way, so the offline evaluation and the served path compute the same function. Decisions reuse
`engine.intent.decide.decide` (per-class thresholds for rare classes, never argmax) and then abstain when the calibrated
max-softmax is below the validation-fitted conservative threshold (`abstain_below`), the rule the sealed-set coverage and
precision numbers are measured under.

torch/transformers are imported inside `TorchInt8Backend` only, so importing the app (and default CI) needs neither.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import structlog
from engine.intent.decide import decide

from app.core.classify.errors import ClassifyUnavailableError

log = structlog.get_logger(__name__)
THRESHOLDS_PATH = Path(__file__).with_name("thresholds_run2_int8_ffn_full.json")
TASKS = ("needs_action", "broad_intent", "sentiment", "urgency")
ABSTAINED = "abstained"
_GRID = 6  # offline probabilities (and the thresholds fitted on them) are on a 6-decimal grid; decide on the same grid


@dataclass(frozen=True)
class TaskSpec:
    classes: tuple[str, ...]
    temperature: float
    rare: dict[str, dict[str, Any]]
    abstain_below: (
        float  # math.inf when the target was never reached on validation (always abstains)
    )


@dataclass(frozen=True)
class Thresholds:
    tasks: dict[str, TaskSpec]
    max_len: int
    head_outputs: (
        int  # the checkpoint head covers all 14 tasks; the 4 served ones are its first columns
    )
    run: str
    variant: str
    sha256: str
    path: str


def load_thresholds(path: Path = THRESHOLDS_PATH) -> Thresholds:
    """Parse the validation-fitted thresholds file; `sha256` is of the raw bytes."""
    raw = path.read_bytes()
    d = json.loads(raw)
    tasks = {
        t: TaskSpec(
            tuple(v["classes"]),
            float(v["temperature"] or 1.0),
            v["rare"],
            math.inf if v["abstain_below"] is None else float(v["abstain_below"]),
        )  # fmt: skip
        for t, v in d["tasks"].items()
    }
    sha = hashlib.sha256(raw).hexdigest()
    return Thresholds(
        tasks, d["max_len"], d["head_outputs"], d["run"], d["variant"], sha, path.name
    )


class LogitsBackend(Protocol):
    def logits(self, texts: list[str]) -> dict[str, np.ndarray]: ...


def softmax(x: np.ndarray) -> np.ndarray:
    """Same arithmetic (and dtype) as `scipy.special.softmax(x, axis=1)`, which the offline pipeline used; scipy is not shipped."""
    e = np.exp(x - x.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)


def decisions(th: Thresholds, logits: dict[str, np.ndarray]) -> list[dict[str, dict[str, Any]]]:
    """Per item, per task: label ('abstained' or a class), candidate_label (the `decide` output), probabilities."""
    n = len(next(iter(logits.values())))
    out: list[dict[str, dict[str, Any]]] = [{} for _ in range(n)]
    for t, spec in th.tasks.items():
        p = np.round(softmax(logits[t] / spec.temperature), _GRID)
        cand = decide(p, spec.classes, spec.rare)
        for i in range(n):
            conf = float(p[i].max())
            abstained = conf < spec.abstain_below
            out[i][t] = {
                "label": ABSTAINED if abstained else spec.classes[cand[i]],
                "candidate_label": spec.classes[cand[i]],
                "confidence": conf,
                "probability": float(p[i, cand[i]]),
                "probabilities": {c: float(p[i, j]) for j, c in enumerate(spec.classes)},
                "abstained": bool(abstained),
                "calibrated": True,
            }
    return out


class TorchInt8Backend:
    """Loads `<dir>/model_int8.pt` (int8_ffn_full state dict) + `<dir>/hf/` (xlm-roberta config and tokenizer files)."""

    def __init__(self, artifact_dir: Path, th: Thresholds, threads: int = 0) -> None:
        pt, hf = artifact_dir / "model_int8.pt", artifact_dir / "hf"
        if not pt.is_file() or not hf.is_dir():
            raise ClassifyUnavailableError(
                "classify_artifacts_missing", f"model artifacts not found under {artifact_dir}"
            )
        try:
            import torch
            from torch import nn
            from transformers import AutoConfig, AutoModel, AutoTokenizer
        except ImportError as e:
            raise ClassifyUnavailableError(
                "classify_runtime_missing", f"torch/transformers not installed: {e}"
            ) from e
        if threads:
            torch.set_num_threads(threads)

        # Same attribute names as engine.intent.model.MultiHeadClassifier, so the state-dict keys match.
        class Net(nn.Module):  # type: ignore[misc]  # torch is absent where mypy runs in CI: Any base
            def __init__(self) -> None:
                super().__init__()
                cfg = AutoConfig.from_pretrained(hf)
                cfg.vocab_size = 8  # the 250k-row table (768 MB in fp32) arrives packed int8 from the state dict; keeps load-time RSS low
                self.enc = AutoModel.from_config(cfg)
                self.head = nn.Linear(self.enc.config.hidden_size, th.head_outputs)

            def forward(self, ids: Any, mask: Any) -> Any:
                h = self.enc(input_ids=ids, attention_mask=mask).last_hidden_state
                m = mask.unsqueeze(-1).to(h.dtype)
                return self.head(((h * m).sum(1) / m.sum(1).clamp(min=1)).float())  # mean pooling

        net = Net().float().eval()
        # Same qconfig spec as engine.intent.quantize.quantize(..., "int8_ffn_full"): per-channel int8 on the feed-forward
        # Linears, uint8 weight-only word embeddings; attention projections and the head stay fp32.
        word = net.enc.get_input_embeddings()
        spec = {
            n: torch.ao.quantization.per_channel_dynamic_qconfig
            for n, mod in net.named_modules()
            if isinstance(mod, nn.Linear) and "attention" not in n and n.startswith("enc.")
            and ("intermediate" in n or n.endswith("output.dense"))
        }  # fmt: skip
        spec[next(n for n, mod in net.named_modules() if mod is word)] = (
            torch.ao.quantization.float_qparams_weight_only_qconfig
        )
        # inplace: the default deep-copies the fp32 encoder first, which doubled the load-time peak
        net = torch.ao.quantization.quantize_dynamic(
            net, qconfig_spec=spec, dtype=torch.qint8, inplace=True
        )
        # weights_only=False: the file is our own build artifact (packed quantized params are not loadable weights-only)
        # mmap=True: pages of the 369 MB file are read on demand instead of being copied into anonymous memory first
        net.load_state_dict(torch.load(pt, map_location="cpu", weights_only=False, mmap=True))
        self._torch, self._net, self._max_len = torch, net.eval(), th.max_len
        self._tok = AutoTokenizer.from_pretrained(hf)
        self._sizes = {t: len(s.classes) for t, s in th.tasks.items()}
        self._lock = (
            threading.Lock()
        )  # one forward at a time: torch already uses all configured threads

    def logits_batched(self, texts: list[str], batch: int) -> dict[str, np.ndarray]:
        """Forward in padded batches of `batch`. Dynamic int8 quantises activations PER TENSOR, so a logit depends on which
        other items share its batch (up to 0.61 in probability on the sealed set, scripts/classify_parity.py). The
        offline evaluation ran batches of 128; this exists to reproduce it. Serving uses `logits`."""
        out: dict[str, list[np.ndarray]] = {t: [] for t in self._sizes}
        for k in range(0, len(texts), batch):
            b = self._tok(
                texts[k : k + batch],
                padding=True,
                truncation=True,
                max_length=self._max_len,
                return_tensors="pt",
            )
            with self._lock, self._torch.inference_mode():
                lg = self._net(b["input_ids"], b["attention_mask"]).float().numpy()
            i = 0
            for t, n in self._sizes.items():
                out[t].append(lg[:, i : i + n])
                i += n
        return {t: np.concatenate(v) for t, v in out.items()}

    def logits(self, texts: list[str]) -> dict[str, np.ndarray]:
        """Serving path: one review per forward (no padding, no co-batched items), so a review's result does not depend on
        the rest of the request. Measured cost: no per-item penalty vs batching on CPU (reports/serving/v2_classify_bench.md)."""
        parts = [self.logits_batched([x], 1) for x in texts]
        return {t: np.concatenate([p[t] for p in parts]) for t in self._sizes}


_state: dict[str, Any] = {}
_state_lock = threading.Lock()


def get_backend(artifact_dir: str, threads: int = 0) -> tuple[LogitsBackend, Thresholds]:
    """Lazily load the model once per process. Raises ClassifyUnavailableError (-> 503) if it cannot be served."""
    with _state_lock:
        if "backend" not in _state:
            t0, th = time.perf_counter(), load_thresholds()
            _state["backend"], _state["th"] = TorchInt8Backend(Path(artifact_dir), th, threads), th
            log.info(
                "classify.model_loaded", load_s=round(time.perf_counter() - t0, 2), threads=threads
            )
        return _state["backend"], _state["th"]
