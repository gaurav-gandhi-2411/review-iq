"""One engine run: train, calibrate, evaluate Track A and (optionally) Track B open-set scorers.

    python -m engine.experiments.run --dataset clinc --model sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \
        --holdout 30 --unknown-head --out reports/engine/clinc_minilm_b.json

All thresholds and the temperature come from VALIDATION only (spec section 4).
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import time
from pathlib import Path

import numpy as np
from sklearn.metrics import confusion_matrix

from engine import data as D
from engine import metrics as M
from engine import scoring as S
from engine.train import TrainConfig, infer, train_classifier

OUTLIER_SOURCE = {"clinc": "banking77", "banking77": "clinc", "massive": "clinc"}


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def load(dataset: str, locales: list[str]) -> D.Splits:
    if dataset == "clinc":
        return D.load_clinc()
    if dataset == "banking77":
        return D.load_banking77()
    return D.load_massive(locales)


def _arrays(rows: list[D.Example], idx: dict[str, int]) -> tuple[list[str], np.ndarray]:
    return [e.text for e in rows], np.array([idx[e.label] for e in rows], dtype=np.int64)


def track_a_block(y: np.ndarray, logits: np.ndarray, names: list[str], t: float) -> dict:
    p = logits.argmax(1)
    labels = list(range(len(names)))
    pc = M.per_class_f1(y, p, labels)
    lo, hi = M.bootstrap_ci(y, p, M.macro_f1)
    return {
        "n": int(len(y)),
        "macro_f1": round(M.macro_f1(y, p, labels), 4),
        "macro_f1_ci95": [round(lo, 4), round(hi, 4)],
        "accuracy": round(M.accuracy(y, p), 4),
        "per_class_f1_min": round(float(pc.min()), 4),
        "per_class_f1_p5": round(float(np.percentile(pc, 5)), 4),
        "worst_classes": [
            {"label": names[i], "f1": round(float(pc[i]), 3)} for i in np.argsort(pc)[:5]
        ],
        "top_confusions": M.top_confusions(y, p, names, 8),
        "per_class_f1": {names[i]: round(float(pc[i]), 4) for i in labels},
        "confusion_matrix": confusion_matrix(y, p, labels=labels).tolist(),  # rows=true, cols=pred
        "temperature": round(t, 4),
    }


def open_set_block(
    name: str,
    s_val: np.ndarray,
    s_known: np.ndarray,
    s_unknown: dict[str, np.ndarray],
    y_known: np.ndarray,
    pred_known: np.ndarray,
    n_cls: int,
) -> dict:
    thr = S.threshold_for_retention(s_val, 0.95)
    keep = s_known >= thr
    labels = list(range(n_cls))
    out = {
        "scorer": name,
        "threshold": round(thr, 5),
        "known_retention": round(float(keep.mean()), 4),
        "known_macro_f1_all": round(M.macro_f1(y_known, pred_known, labels), 4),
        "known_macro_f1_answered": round(M.macro_f1(y_known[keep], pred_known[keep], labels), 4),
        "known_accuracy_answered": round(M.accuracy(y_known[keep], pred_known[keep]), 4),
    }
    for uname, su in s_unknown.items():
        out[uname] = {
            "n": int(len(su)),
            "rejection_recall": round(float((su < thr).mean()), 4),
            **{k: round(v, 4) for k, v in M.known_vs_unknown(s_known, su).items()},
            **M.bootstrap_open_set(s_known, su, thr),
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=["clinc", "banking77", "massive"], required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--locales", default="en-US")
    ap.add_argument(
        "--train-locales", default=None, help="massive: train locales (default=locales)"
    )
    ap.add_argument("--holdout", type=int, default=0)
    ap.add_argument("--unknown-head", action="store_true")
    ap.add_argument("--n-train", type=int, default=0, help="stratified subsample size (0=all)")
    ap.add_argument("--epochs", type=int, default=6)
    ap.add_argument("--lr", type=float, default=5e-5)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--class-balanced", action="store_true")
    ap.add_argument("--char-noise", type=float, default=0.0)
    ap.add_argument("--no-freeze-embeddings", action="store_true")
    ap.add_argument("--export-dir", default=None, help="write ONNX int8 serving artifacts here")
    ap.add_argument(
        "--scorer", default="msp_calibrated", choices=["msp_calibrated", "energy", "mahalanobis"]
    )
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    t0 = time.time()
    locales = a.locales.split(",")
    sp = load(a.dataset, locales)
    train_rows, val_rows, test_rows = sp.train, sp.val, sp.test
    if a.train_locales:
        keep_l = set(a.train_locales.split(","))
        train_rows = [e for e in train_rows if e.lang in keep_l]
        val_rows = [e for e in val_rows if e.lang in keep_l]

    unknown_classes: list[str] = []
    if a.holdout:
        unknown_classes = D.holdout_classes(sp.labels, a.holdout)
    known_labels = [lab for lab in sp.labels if lab not in set(unknown_classes)]
    idx = {lab: i for i, lab in enumerate(known_labels)}
    isk = lambda e: e.label in idx  # noqa: E731
    train_rows = [e for e in train_rows if isk(e)]
    val_k = [e for e in val_rows if isk(e)]
    test_k = [e for e in test_rows if isk(e)]
    test_u = [e for e in test_rows if not isk(e)]
    if a.n_train:
        train_rows = D.stratified_subsample(train_rows, a.n_train)

    tx, ty = _arrays(train_rows, idx)
    vx, vy = _arrays(val_k, idx)
    sx, sy = _arrays(test_k, idx)
    cfg = TrainConfig(
        model=a.model, epochs=a.epochs, lr=a.lr, batch_size=a.batch_size, seed=a.seed,
        class_balanced=a.class_balanced, char_noise=a.char_noise,
        freeze_embeddings=not a.no_freeze_embeddings,
    )  # fmt: skip
    n_cls = len(known_labels)
    model, tok, info = train_classifier(cfg, tx, ty, n_cls, vx, vy)

    v_log, v_emb = infer(model, tok, vx)
    s_log, s_emb = infer(model, tok, sx)
    t = S.fit_temperature(v_log, vy)
    result: dict = {
        "commit": _git_sha(), "dataset": a.dataset, "model": a.model, "seed": a.seed,
        "n_train": len(tx), "n_classes": n_cls, "locales": locales,
        "train_info": info, "config": vars(a),
    }  # fmt: skip
    result["track_a"] = track_a_block(sy, s_log, known_labels, t)
    # Per-item test predictions + confidence scores, for precision-at-coverage (selective.py) and for
    # pairing with the LLM baseline on the same items. test_idx indexes the dataset's test split.
    _, tr_emb0 = infer(model, tok, tx)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        Path(a.out).with_suffix(".npz"),
        y=sy, pred=s_log.argmax(1), msp=S.msp(s_log, t), energy=S.energy(s_log, t),
        maha=S.Mahalanobis(tr_emb0, ty, n_cls).score(s_emb),
        test_idx=np.array([i for i, e in enumerate(test_rows) if isk(e)]),
    )  # fmt: skip
    if a.dataset == "massive" and len(locales) > 1:
        by = {}
        for loc in locales:
            sel = np.array([e.lang == loc for e in test_k])
            by[loc] = track_a_block(sy[sel], s_log[sel], known_labels, t)["macro_f1"]
        result["track_a_by_locale_macro_f1"] = by
        non_en = np.array([e.lang != "en-US" for e in test_k])
        if non_en.any():
            result["track_a_non_english"] = track_a_block(
                sy[non_en], s_log[non_en], known_labels, t
            )

    # ---- open set -----------------------------------------------------------------------
    unknown_sets: dict[str, list[D.Example]] = {}
    if a.holdout:
        unknown_sets["held_out_classes"] = test_u
        result["held_out_classes"] = unknown_classes
    if a.dataset == "clinc":
        unknown_sets["clinc_oos_test"] = sp.extra["oos_test"]
    if unknown_sets:
        u_out = {k: infer(model, tok, [e.text for e in v]) for k, v in unknown_sets.items()}
        _, tr_emb = infer(model, tok, tx)
        maha = S.Mahalanobis(tr_emb, ty, n_cls)
        pred = s_log.argmax(1)
        scorers = {
            "msp_raw": (S.msp(v_log), S.msp(s_log), {k: S.msp(u[0]) for k, u in u_out.items()}),
            "msp_calibrated": (
                S.msp(v_log, t), S.msp(s_log, t), {k: S.msp(u[0], t) for k, u in u_out.items()}
            ),
            "energy": (S.energy(v_log, t), S.energy(s_log, t), {k: S.energy(u[0], t) for k, u in u_out.items()}),
            "mahalanobis": (maha.score(v_emb), maha.score(s_emb), {k: maha.score(u[1]) for k, u in u_out.items()}),
        }  # fmt: skip
        result["open_set"] = [
            open_set_block(n, sv, sk, su, sy, pred, n_cls) for n, (sv, sk, su) in scorers.items()
        ]
        if a.unknown_head:
            src = D.load_clinc() if OUTLIER_SOURCE[a.dataset] == "clinc" else D.load_banking77()
            rng = random.Random(D.SEED)
            outl = [e.text for e in rng.sample(src.train, min(1500, len(src.train)))]
            tx2 = tx + outl
            ty2 = np.r_[ty, np.full(len(outl), n_cls)]
            m2, tok2, info2 = train_classifier(cfg, tx2, ty2, n_cls + 1, vx, vy)

            def known_score(lg: np.ndarray) -> np.ndarray:
                from scipy.special import softmax

                return 1.0 - softmax(lg, axis=1)[:, n_cls]

            vl2, _ = infer(m2, tok2, vx)
            sl2, _ = infer(m2, tok2, sx)
            u2 = {k: infer(m2, tok2, [e.text for e in v])[0] for k, v in unknown_sets.items()}
            p2 = sl2[:, :n_cls].argmax(1)
            result["open_set"].append(
                open_set_block(
                    "unknown_head",
                    known_score(vl2),
                    known_score(sl2),
                    {k: known_score(v) for k, v in u2.items()},
                    sy,
                    p2,
                    n_cls,
                )  # fmt: skip
            )
            result["unknown_head_train_info"] = info2
    if a.export_dir:
        from engine.export import export

        _, tr_emb2 = infer(model, tok, tx)
        maha2 = S.Mahalanobis(tr_emb2, ty, n_cls)
        sv = {
            "msp_calibrated": S.msp(v_log, t), "energy": S.energy(v_log, t), "mahalanobis": maha2.score(v_emb),
        }[a.scorer]  # fmt: skip
        thr = S.threshold_for_retention(sv, 0.95)
        export(model, tok, a.export_dir, known_labels, t, thr, a.scorer, maha2, vx)
        result["exported_to"] = str(a.export_dir)
        result["export_scorer"] = {"name": a.scorer, "threshold": thr, "temperature": t}
    result["wall_seconds"] = round(time.time() - t0, 1)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    ta = result["track_a"]
    print(json.dumps({k: ta[k] for k in ("n", "macro_f1", "macro_f1_ci95", "accuracy")}))
    for r in result.get("open_set", []):
        print(r["scorer"], {k: v for k, v in r.items() if k != "scorer"})


if __name__ == "__main__":
    main()
