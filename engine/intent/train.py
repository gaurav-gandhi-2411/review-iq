"""Train the multi-task review-intent model and write per-task predictions.

    python -m engine.intent.train --labels <dir>/labels.json --texts D:/ml-cache/review-intent/scale.jsonl \
        --manifest reports/labelling/scale/manifest.json --out-dir reports/intent/run1 [--device cpu]

Recipe = E2 (`engine.train`): multilingual-e5-base, mean pooling, AdamW lr 5e-5, batch 32, linear warm-up (6 percent)
and decay, gradient clipping 1.0, fp16 autocast on CUDA, frozen fp16 word embeddings on CUDA, seed 42, NOT class
weighted (E2's default; selective prediction ranks by calibrated confidence so no re-weighting is needed), no
`query:` prefix (E2 used none). Differences, stated: 6 epochs with patience-2 early stopping on validation mean
macro-F1 across tasks (E2 kept the best of a fixed number of epochs); max length 128 tokens instead of E2's 64 (reviews
are longer than banking utterances); loss = sum over tasks of the mean cross-entropy over the items labelled for that
task (missing labels are masked with -100). Temperature per task is fitted on validation only (`engine.scoring`).
Run 2 (S23) changes ONLY the training length: `--epochs 30 --patience 3`, every other setting as above. The linear
lr decay is scheduled over the epoch budget, so it is slower than run 1's (part of 'more epochs'). Early stopping
uses validation only; `epoch_log.json` holds the per-epoch validation curve.
Outputs: pred_<task>.json (ids and calibrated probabilities for val and test) and metadata.json. No review text.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import torch
from scipy.special import softmax
from torch import nn

from engine import metrics as M
from engine import scoring as SC
from engine.experiments.run import _git_sha
from engine.intent import decide as D
from engine.intent import safety_data as SD
from engine.intent.labels import load_texts
from engine.intent.model import E5, MultiHeadClassifier, load_pretrained
from engine.train import _batches, seed_everything

IGNORE = -100
Loader = Callable[[str, dict[str, int]], tuple[MultiHeadClassifier, object]]


@dataclass
class IntentConfig:
    model: str = E5
    epochs: int = 6
    lr: float = 5e-5
    batch_size: int = 32
    seed: int = 42
    max_len: int = 128
    patience: int = 2
    freeze_embeddings: bool = True


def build_arrays(
    ids: list[str], labels: dict[str, dict[str, str]], classes: dict[str, tuple[str, ...]]
) -> dict[str, np.ndarray]:
    """task -> int array over `ids`, IGNORE where the item has no label for that task."""
    return {
        t: np.array(
            [classes[t].index(labels[t][i]) if i in labels[t] else IGNORE for i in ids],
            dtype=np.int64,
        )
        for t in classes
    }


def masked_loss(logits: dict[str, torch.Tensor], y: dict[str, torch.Tensor]) -> torch.Tensor:
    """Sum over tasks of mean CE over labelled items; a task with no label in the batch contributes nothing."""
    total = torch.zeros((), device=next(iter(logits.values())).device)
    for t, lg in logits.items():
        if (y[t] != IGNORE).any():
            total = total + nn.functional.cross_entropy(lg.float(), y[t], ignore_index=IGNORE)
    return total


def _encode(tok, texts: list[str], max_len: int, device: str):  # noqa: ANN001, ANN202
    b = tok(texts, padding=True, truncation=True, max_length=max_len, return_tensors="pt")
    return b["input_ids"].to(device), b["attention_mask"].to(device)


@torch.no_grad()
def infer(
    model: MultiHeadClassifier,
    tok,
    texts: list[str],
    cfg: IntentConfig,
    device: str,  # noqa: ANN001
) -> dict[str, np.ndarray]:
    """task -> raw logits for every text."""
    model.eval()
    parts: dict[str, list[np.ndarray]] = {t: [] for t in model.sizes}
    for i in range(0, len(texts), 128):
        ids, mask = _encode(tok, texts[i : i + 128], cfg.max_len, device)
        with torch.autocast(device_type="cuda", dtype=torch.float16, enabled=device == "cuda"):
            lg, _ = model(ids, mask)
        for t, v in model.split(lg.float()).items():
            parts[t].append(v.cpu().numpy())
    return {t: np.concatenate(v) if v else np.zeros((0, model.sizes[t])) for t, v in parts.items()}


def per_task_macro_f1(logits: dict[str, np.ndarray], y: dict[str, np.ndarray]) -> dict[str, float]:
    """task -> macro-F1 on the validation items labelled for it (tasks with no validation label omitted)."""
    out = {}
    for t, lg in logits.items():
        m = y[t] != IGNORE
        if m.any():
            out[t] = M.macro_f1(y[t][m], lg[m].argmax(1))
    return out


def mean_macro_f1(logits: dict[str, np.ndarray], y: dict[str, np.ndarray]) -> float:
    """Mean over tasks with validation labels of macro-F1 on the labelled items."""
    f1s = list(per_task_macro_f1(logits, y).values())
    return float(np.mean(f1s)) if f1s else 0.0


def fit(
    cfg: IntentConfig, model: MultiHeadClassifier, tok, train: tuple[list[str], dict],  # noqa: ANN001
    val: tuple[list[str], dict], device: str, log_path: Path | None = None,
) -> dict:  # fmt: skip
    """Train in place, restore the best-validation weights, return the history.

    Early stopping looks at VALIDATION only (never the sealed set). When `log_path` is given the per-epoch history
    (mean and per-task validation macro-F1) is rewritten after every epoch so a killed run still leaves its curve."""
    seed_everything(cfg.seed)
    rng = random.Random(cfg.seed)
    if cfg.freeze_embeddings:
        we = model.enc.get_input_embeddings()
        we.weight.requires_grad = False
        if device == "cuda":
            we.half()  # E2: frozen fp16 word embeddings (fp16 on CPU has no matmul)
    model.to(device)
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=cfg.lr, weight_decay=0.01)
    tx, ty = train
    steps = cfg.epochs * ((len(tx) + cfg.batch_size - 1) // cfg.batch_size)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, (s + 1) / max(1, int(0.06 * steps))) * max(0.0, 1 - s / steps)
    )
    scaler = torch.amp.GradScaler(enabled=device == "cuda")
    best, best_state, bad, history = -1.0, None, 0, []
    for epoch in range(cfg.epochs):
        model.train()
        loss = torch.zeros(())
        for batch in _batches(len(tx), cfg.batch_size, rng):
            ids, mask = _encode(tok, [tx[i] for i in batch], cfg.max_len, device)
            y = {t: torch.tensor(v[batch], device=device) for t, v in ty.items()}
            with torch.autocast(device_type="cuda", dtype=torch.float16, enabled=device == "cuda"):
                lg, _ = model(ids, mask)
            loss = masked_loss(model.split(lg.float()), y)
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            nn.utils.clip_grad_norm_(params, 1.0)
            scaler.step(opt)
            scaler.update()
            sched.step()
        per_task = per_task_macro_f1(infer(model, tok, val[0], cfg, device), val[1])
        f1 = float(np.mean(list(per_task.values()))) if per_task else 0.0
        history.append({
            "epoch": epoch + 1, "val_mean_macro_f1": round(f1, 4), "loss": float(loss),
            "lr": float(sched.get_last_lr()[0]),
            "val_macro_f1_by_task": {t: round(v, 4) for t, v in per_task.items()},
        })  # fmt: skip
        if log_path is not None:
            log_path.write_text(json.dumps(history, indent=1) + "\n", encoding="utf-8")
        print(f"epoch {epoch + 1}/{cfg.epochs} val_mean_macro_f1={f1:.4f}", flush=True)
        if f1 > best:
            best, bad = f1, 0
            best_state = copy.deepcopy({k: v.cpu() for k, v in model.state_dict().items()})
        else:
            bad += 1
            if bad >= cfg.patience:
                break
    model.load_state_dict(best_state)
    return {
        "history": history,
        "best_val_mean_macro_f1": best,
        "best_epoch": max(history, key=lambda h: h["val_mean_macro_f1"])["epoch"],
        "stopped_early": len(history) < cfg.epochs,
    }


def write_predictions(
    out_dir: Path, classes: dict[str, tuple[str, ...]], arr: dict, logits: dict[str, dict],
    decisions: dict[str, dict] | None = None,
) -> dict[str, float | None]:  # fmt: skip
    """Fit a per-task temperature on validation only and write pred_<task>.json for val and test.

    arr[split] = (ids, {task: labels}); logits[split][task] = raw logits. -> task -> temperature
    (None = fewer than 2 validation labels). Shared with `engine.intent.quantize`. The decision thresholds of rare classes
    (`engine.intent.decide`, the serving default) are fitted here on the same calibrated VALIDATION probabilities, written
    into each pred file under "decision" and, when `decisions` is given, collected there as task -> {class: record}."""
    temps: dict[str, float | None] = {}
    for t, cl in classes.items():
        yv = arr["val"][1][t]
        m = yv != IGNORE
        temp = SC.fit_temperature(logits["val"][t][m], yv[m]) if m.sum() >= 2 else None
        temps[t] = temp
        splits = {
            s: {
                "ids": arr[s][0],
                "probs": np.round(softmax(logits[s][t] / (temp or 1.0), axis=1), 6).tolist(),
            }
            for s in ("val", "test")
        }
        pv = np.array(splits["val"]["probs"]).reshape(len(splits["val"]["ids"]), len(cl))
        thr = D.fit_thresholds(yv[m], pv[m], cl, D.rare_for(t))
        if decisions is not None and thr:
            decisions[t] = thr
        (out_dir / f"pred_{t.replace(':', '__')}.json").write_text(
            json.dumps({
                "task": t, "classes": list(cl), "temperature": temp,
                "decision": {"mode": D.DEFAULT_MODE, "thresholds": thr}, "splits": splits,
            })  # fmt: skip
            + "\n",
            encoding="utf-8",
        )
    return temps


def run(
    cfg: IntentConfig, data: dict, texts: dict[str, str], out_dir: Path, device: str,
    loader: Loader = load_pretrained, save_weights: bool = False, inputs: dict | None = None,
    extra: dict | None = None,
) -> dict:  # fmt: skip
    """data = {"classes", "labels", "ids": {split: [id]}} -> writes predictions + metadata, returns metadata.

    `loader(name, sizes) -> (model, tokenizer)` is injectable so tests run a stub encoder offline."""
    t0 = time.time()
    classes = {t: tuple(c) for t, c in data["classes"].items()}
    model, tok = loader(cfg.model, {t: len(c) for t, c in classes.items()})
    arr, txt = {}, {}
    for s in ("train", "val", "test"):
        ids = [i for i in data["ids"][s] if i in texts]
        if s == "train":  # items with no label on any task teach nothing
            ids = [i for i in ids if any(i in data["labels"][s][t] for t in classes)]
        arr[s] = (ids, build_arrays(ids, data["labels"][s], classes))
        txt[s] = [texts[i] for i in ids]
    out_dir.mkdir(parents=True, exist_ok=True)
    info = fit(
        cfg, model, tok, (txt["train"], arr["train"][1]), (txt["val"], arr["val"][1]), device,
        out_dir / "epoch_log.json",
    )  # fmt: skip
    if save_weights:  # best-validation weights, fp32, for the int8 export; far too large to commit
        torch.save(
            {k: v.float().cpu() for k, v in model.state_dict().items()}, out_dir / "model.pt"
        )
    logits = {s: infer(model, tok, txt[s], cfg, device) for s in ("val", "test")}
    decisions: dict[str, dict] = {}
    temps = write_predictions(out_dir, classes, arr, logits, decisions)
    meta = {
        "commit": _git_sha(), "config": asdict(cfg), "device": device, "torch": torch.__version__,
        "n_items": {s: len(arr[s][0]) for s in arr}, "temperatures": temps,
        "n_labelled": {s: {t: int((v != IGNORE).sum()) for t, v in arr[s][1].items()} for s in arr},
        "calibration": "per-task temperature on validation only; null = fewer than 2 validation labels",
        "decision": {"mode": D.DEFAULT_MODE, "thresholds": decisions, "fitted_on": "validation only"},
        "train_info": info, "seconds": round(time.time() - t0, 1), "inputs_sha256_16": inputs or {},
    } | (extra or {})  # fmt: skip
    (out_dir / "metadata.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    return meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", required=True, help="labels.json from engine.intent.labels")
    ap.add_argument("--texts", required=True, help="private scale.jsonl")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--model", default=E5)
    ap.add_argument("--epochs", type=int, default=6)
    ap.add_argument("--lr", type=float, default=5e-5)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--max-len", type=int, default=128)
    ap.add_argument("--patience", type=int, default=2)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--save-weights", action="store_true", help="write the best checkpoint to out-dir/model.pt"
    )
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    ap.add_argument(
        "--safety-stratum", default="exclude", choices=["include", "exclude"],
        help="include: add the safety stratum's TRAIN items (spec Amendment 7); val/test never enter training",
    )  # fmt: skip
    ap.add_argument("--safety-split", default="reports/labelling/safety/split.json")
    ap.add_argument("--safety-labels", default="reports/labelling/safety/labels.jsonl")
    ap.add_argument("--safety-texts", help="private safety_items.jsonl (required with include)")
    a = ap.parse_args()
    cfg = IntentConfig(a.model, a.epochs, a.lr, a.batch_size, a.seed, a.max_len, a.patience)
    lab = json.loads(Path(a.labels).read_text(encoding="utf-8"))
    man = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    data = {
        "classes": lab["classes"],
        "labels": lab["labels"],
        "ids": {s: [r["id"] for r in man["items"] if r["split"] == s] for s in lab["labels"]},
    }
    device = ("cuda" if torch.cuda.is_available() else "cpu") if a.device == "auto" else a.device
    paths = [a.labels, a.texts, a.manifest]
    texts = load_texts(Path(a.texts))
    extra: dict = {"safety_stratum": "exclude"}
    if a.safety_stratum == "include":
        if not a.safety_texts:
            ap.error("--safety-stratum include needs --safety-texts")
        from engine.labelling import leakage_ledger as LL

        sp = json.loads(Path(a.safety_split).read_text(encoding="utf-8"))
        data, texts, info = SD.add_safety_train(
            data, texts, sp, SD.load_jsonl(Path(a.safety_labels)),
            SD.load_jsonl(Path(a.safety_texts)), LL.sealed_hashes(),
        )  # fmt: skip
        extra = {"safety_stratum": "include", "safety_train": info}
        paths += [a.safety_split, a.safety_labels, a.safety_texts]
    inputs = {Path(p).name: hashlib.sha256(Path(p).read_bytes()).hexdigest()[:16] for p in paths}
    run(
        cfg, data, texts, Path(a.out_dir), device,
        save_weights=a.save_weights, inputs=inputs, extra=extra,
    )  # fmt: skip


if __name__ == "__main__":
    main()
