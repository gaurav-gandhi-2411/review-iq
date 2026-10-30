"""E2d: flat vs hierarchical loss vs domain-constrained prediction, and where the errors live.

One encoder is trained twice: flat (intent head only) and joint (intent head plus a parent head,
loss = CE(intent) + 0.5 * CE(parent)). The joint model is evaluated two ways: unconstrained intent
argmax, and constrained (intent logits masked to the predicted parent's intents).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from engine import metrics as M
from engine.experiments.run import _arrays, _git_sha, load
from engine.train import TrainConfig, infer, train_classifier


def _report(y: np.ndarray, p: np.ndarray, par_of: np.ndarray, n: int) -> dict:
    wrong = p != y
    cross = wrong & (par_of[p] != par_of[y])
    lo, hi = M.bootstrap_ci(y, p, M.macro_f1)
    return {
        "macro_f1": round(M.macro_f1(y, p, list(range(n))), 4),
        "macro_f1_ci95": [round(lo, 4), round(hi, 4)],
        "accuracy": round(M.accuracy(y, p), 4),
        "errors": int(wrong.sum()),
        "errors_within_parent": int((wrong & ~cross).sum()),
        "errors_cross_parent": int(cross.sum()),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=["clinc", "massive"], required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--locales", default="en-US")
    ap.add_argument("--epochs", type=int, default=6)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    sp = load(a.dataset, a.locales.split(","))
    names = sp.labels
    idx = {lab: i for i, lab in enumerate(names)}
    parents = sorted({sp.parent[lab] for lab in names})
    pidx = {p: i for i, p in enumerate(parents)}
    par_of = np.array([pidx[sp.parent[lab]] for lab in names])
    n, npar = len(names), len(parents)
    tx, ty = _arrays(sp.train, idx)
    vx, vy = _arrays(sp.val, idx)
    sx, sy = _arrays(sp.test, idx)
    cfg = TrainConfig(model=a.model, epochs=a.epochs, seed=a.seed)

    out: dict = {"commit": _git_sha(), "dataset": a.dataset, "model": a.model, "seed": a.seed,
                 "n_intents": n, "n_parents": npar}  # fmt: skip
    m, tok, _ = train_classifier(cfg, tx, ty, n, vx, vy)
    lg, _ = infer(m, tok, sx)
    out["flat"] = _report(sy, lg[:, :n].argmax(1), par_of, n)

    m2, tok2, _ = train_classifier(cfg, tx, ty, n, vx, vy, aux_y=par_of[ty], n_aux=npar)
    lg2, _ = infer(m2, tok2, sx)
    intent, parent = lg2[:, :n], lg2[:, n:]
    out["joint_unconstrained"] = _report(sy, intent.argmax(1), par_of, n)
    out["joint_parent_accuracy"] = round(float((parent.argmax(1) == par_of[sy]).mean()), 4)
    masked = np.where(par_of[None, :] == parent.argmax(1)[:, None], intent, -1e9)
    out["joint_constrained"] = _report(sy, masked.argmax(1), par_of, n)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out))


if __name__ == "__main__":
    main()
