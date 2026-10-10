"""Fine-tune an encoder + linear head; return logits and pooled embeddings for offline scoring.

VRAM note (spec section 6): ~2 GB is usable on the shared RTX 3070, so word embeddings are frozen
and stored in fp16 (they are 70-85% of the parameters of a multilingual encoder and barely move in
fine-tuning), and the rest trains under fp16 autocast.
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass

import numpy as np
import torch
from torch import nn
from transformers import AutoModel, AutoTokenizer

from engine.metrics import macro_f1

MAX_LEN = 64  # spec: short texts


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


class Classifier(nn.Module):
    def __init__(self, name: str, n_out: int) -> None:
        super().__init__()
        self.enc = AutoModel.from_pretrained(name)
        self.head = nn.Linear(self.enc.config.hidden_size, n_out)

    def forward(self, ids: torch.Tensor, mask: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        h = self.enc(input_ids=ids, attention_mask=mask).last_hidden_state
        m = mask.unsqueeze(-1).to(h.dtype)
        emb = (h * m).sum(1) / m.sum(1).clamp(min=1)  # mean pooling
        return self.head(emb.float()), emb.float()


@dataclass
class TrainConfig:
    model: str
    epochs: int = 6
    lr: float = 5e-5
    batch_size: int = 32
    seed: int = 42
    class_balanced: bool = False
    char_noise: float = 0.0  # probability per character of an edit; spec section 3 (noisy text)
    freeze_embeddings: bool = True


def _noise(text: str, p: float, rng: random.Random) -> str:
    if p <= 0:
        return text
    out = []
    for ch in text:
        r = rng.random()
        if r < p / 3:
            continue  # drop
        out.append(ch)
        if p / 3 <= r < 2 * p / 3:
            out.append(ch)  # duplicate
    return "".join(out) or text


def _batches(n: int, bs: int, rng: random.Random) -> list[list[int]]:
    idx = list(range(n))
    rng.shuffle(idx)
    return [idx[i : i + bs] for i in range(0, n, bs)]


def encode(tok, texts: list[str], device: str) -> tuple[torch.Tensor, torch.Tensor]:
    b = tok(texts, padding=True, truncation=True, max_length=MAX_LEN, return_tensors="pt")
    return b["input_ids"].to(device), b["attention_mask"].to(device)


@torch.no_grad()
def infer(
    model: Classifier, tok, texts: list[str], device: str = "cuda", bs: int = 128
) -> tuple[np.ndarray, np.ndarray]:
    model.eval()
    logits, embs = [], []
    for i in range(0, len(texts), bs):
        ids, mask = encode(tok, texts[i : i + bs], device)
        with torch.autocast(device_type="cuda", dtype=torch.float16, enabled=device == "cuda"):
            lg, em = model(ids, mask)
        logits.append(lg.float().cpu().numpy())
        embs.append(em.float().cpu().numpy())
    return np.concatenate(logits), np.concatenate(embs)


def train_classifier(
    cfg: TrainConfig,
    train_texts: list[str],
    train_y: np.ndarray,
    n_out: int,
    val_texts: list[str],
    val_y: np.ndarray,
    device: str = "cuda",
    aux_y: np.ndarray | None = None,
    n_aux: int = 0,
    aux_weight: float = 0.5,
) -> tuple[Classifier, object, dict]:
    """aux_y/n_aux: optional parent-label head trained jointly (hierarchical loss, spec E2d).

    The first n_out logits are the intent logits; the next n_aux are the parent logits.
    """
    seed_everything(cfg.seed)
    rng = random.Random(cfg.seed)
    tok = AutoTokenizer.from_pretrained(cfg.model)
    model = Classifier(cfg.model, n_out + n_aux)
    if cfg.freeze_embeddings:
        we = model.enc.get_input_embeddings()
        we.weight.requires_grad = False
        we.half()
    model.to(device)
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=cfg.lr, weight_decay=0.01)
    steps = cfg.epochs * ((len(train_texts) + cfg.batch_size - 1) // cfg.batch_size)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, (s + 1) / max(1, int(0.06 * steps))) * max(0.0, 1 - s / steps)
    )
    scaler = torch.amp.GradScaler(enabled=device == "cuda")
    weights = None
    if cfg.class_balanced:
        counts = np.bincount(train_y, minlength=n_out).clip(min=1)
        weights = torch.tensor(counts.sum() / (n_out * counts), dtype=torch.float32, device=device)
    loss_fn = nn.CrossEntropyLoss(weight=weights)
    best = (-1.0, None)
    history = []
    for epoch in range(cfg.epochs):
        model.train()
        for batch in _batches(len(train_texts), cfg.batch_size, rng):
            texts = [_noise(train_texts[i], cfg.char_noise, rng) for i in batch]
            ids, mask = encode(tok, texts, device)
            y = torch.tensor(train_y[batch], device=device)
            with torch.autocast(device_type="cuda", dtype=torch.float16, enabled=device == "cuda"):
                logits, _ = model(ids, mask)
            logits = logits.float()
            loss = loss_fn(logits[:, :n_out], y)
            if aux_y is not None:
                ay = torch.tensor(aux_y[batch], device=device)
                loss = loss + aux_weight * nn.functional.cross_entropy(logits[:, n_out:], ay)
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            nn.utils.clip_grad_norm_(params, 1.0)
            scaler.step(opt)
            scaler.update()
            sched.step()
        vl, _ = infer(model, tok, val_texts, device)
        f1 = macro_f1(val_y, vl[:, :n_out].argmax(1))
        history.append({"epoch": epoch + 1, "val_macro_f1": round(f1, 4), "loss": float(loss)})
        print(f"epoch {epoch + 1}/{cfg.epochs} val_macro_f1={f1:.4f}", flush=True)
        if f1 > best[0]:
            best = (f1, copy.deepcopy({k: v.cpu() for k, v in model.state_dict().items()}))
    model.load_state_dict(best[1])
    return model, tok, {"history": history, "best_val_macro_f1": best[0]}
