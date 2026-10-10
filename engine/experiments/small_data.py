"""E2e: the ~500-row regime. Methods compared on the same sealed test set:

  plain        fine-tune on a stratified 500-row subsample (round-robin over labels)
  char_noise   same, with character drop/duplicate noise applied each epoch
  backtrans    500 rows + one en->de->en paraphrase each (Helsinki-NLP opus-mt), 1,000 rows
  emb_logreg   frozen pretrained encoder embeddings + logistic regression (no fine-tuning)
  active       uncertainty (least-confidence) sampling: 100 random, then +100 per round to 500
  random500    the plain baseline restated for the active-learning comparison

Selection uses the validation split only. Seeds: 42.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import numpy as np
import torch
from scipy.special import softmax
from sklearn.linear_model import LogisticRegression

from engine import data as D
from engine import metrics as M
from engine.experiments.run import _arrays, _git_sha, load
from engine.train import TrainConfig, infer, seed_everything, train_classifier

EPOCHS = 15  # 500 rows is ~16 steps per epoch; more epochs are needed than on the full data


def _eval(m, tok, sx, sy, n) -> dict:
    lg, _ = infer(m, tok, sx)
    p = lg[:, :n].argmax(1)
    lo, hi = M.bootstrap_ci(sy, p, M.macro_f1)
    return {"macro_f1": round(M.macro_f1(sy, p, list(range(n))), 4),
            "macro_f1_ci95": [round(lo, 4), round(hi, 4)], "accuracy": round(M.accuracy(sy, p), 4)}  # fmt: skip


def backtranslate(texts: list[str], device: str = "cuda") -> list[str]:
    from transformers import MarianMTModel, MarianTokenizer

    def run(name: str, xs: list[str]) -> list[str]:
        tk = MarianTokenizer.from_pretrained(name)
        mt = MarianMTModel.from_pretrained(name).to(device).half().eval()
        out: list[str] = []
        for i in range(0, len(xs), 32):
            b = tk(
                xs[i : i + 32], return_tensors="pt", padding=True, truncation=True, max_length=64
            )
            with torch.no_grad():
                g = mt.generate(
                    **{k: v.to(device) for k, v in b.items()}, num_beams=2, max_length=64
                )
            out += tk.batch_decode(g, skip_special_tokens=True)
        del mt
        torch.cuda.empty_cache()
        return out

    return run("Helsinki-NLP/opus-mt-de-en", run("Helsinki-NLP/opus-mt-en-de", texts))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=["clinc", "banking77"], required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--methods", default="plain,char_noise,emb_logreg,backtrans,active")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    sp = load(a.dataset, ["en-US"])
    names = sp.labels
    idx = {lab: i for i, lab in enumerate(names)}
    n = len(names)
    sub = D.stratified_subsample(sp.train, a.n)
    tx, ty = _arrays(sub, idx)
    vx, vy = _arrays(sp.val, idx)
    sx, sy = _arrays(sp.test, idx)
    res: dict = {"commit": _git_sha(), "dataset": a.dataset, "model": a.model, "n_train": len(sub),
                 "rows_per_class": round(len(sub) / n, 2)}  # fmt: skip
    base = TrainConfig(model=a.model, epochs=EPOCHS, lr=8e-5, batch_size=16)
    for method in a.methods.split(","):
        if method in ("plain", "char_noise"):
            cfg = TrainConfig(
                **{**base.__dict__, "char_noise": 0.05 if method == "char_noise" else 0.0}
            )
            m, tok, _ = train_classifier(cfg, tx, ty, n, vx, vy)
            res[method] = _eval(m, tok, sx, sy, n)
        elif method == "emb_logreg":
            from transformers import AutoTokenizer

            from engine.train import Classifier  # noqa: F401

            tok = AutoTokenizer.from_pretrained(a.model)
            m = Classifier(a.model, n).cuda()  # head untouched; only the pooled embeddings are used
            _, e_tr = infer(m, tok, tx)
            _, e_te = infer(m, tok, sx)
            clf = LogisticRegression(max_iter=2000, C=10.0).fit(e_tr, ty)
            p = clf.predict(e_te)
            lo, hi = M.bootstrap_ci(sy, p, M.macro_f1)
            res[method] = {"macro_f1": round(M.macro_f1(sy, p, list(range(n))), 4),
                           "macro_f1_ci95": [round(lo, 4), round(hi, 4)], "accuracy": round(M.accuracy(sy, p), 4)}  # fmt: skip
        elif method == "backtrans":
            aug = backtranslate(tx)
            tx2 = tx + aug
            ty2 = np.r_[ty, ty]
            m, tok, _ = train_classifier(base, tx2, ty2, n, vx, vy)
            res[method] = _eval(m, tok, sx, sy, n)
            res["backtrans_examples"] = [{"src": tx[i], "bt": aug[i]} for i in range(3)]
        elif method == "active":
            seed_everything(42)
            rng = random.Random(42)
            cur = D.stratified_subsample(sp.train, 100)
            rest = [e for e in sp.train if e not in set(cur)]
            curve = []
            while True:
                cx, cy = _arrays(cur, idx)
                m, tok, _ = train_classifier(base, cx, cy, n, vx, vy)
                curve.append({"n": len(cur), **_eval(m, tok, sx, sy, n)})
                if len(cur) >= a.n:
                    break
                cand = rng.sample(rest, min(len(rest), 3000))
                lg, _ = infer(m, tok, [e.text for e in cand])
                conf = softmax(lg[:, :n], axis=1).max(1)
                pick = [cand[i] for i in np.argsort(conf)[:100]]
                cur += pick
                ps = set(pick)
                rest = [e for e in rest if e not in ps]
            res["active"] = curve
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k not in ("backtrans_examples",)}))


if __name__ == "__main__":
    main()
