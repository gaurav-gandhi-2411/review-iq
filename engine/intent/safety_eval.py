"""Safety-stratum evaluation of `urgency = high` (spec Amendments 6 and 7). ENRICHED, KEYWORD-MINED, SILVER (panel) labels:
never pooled with the Amendment 5 sealed set, not a prevalence or base-rate number.

    python -m engine.intent.safety_eval predict --weights D:/ml-cache/s23/run3/model.pt --meta reports/intent/run3/metadata.json \
        --labels reports/intent/labels/labels.json --texts D:/ml-cache/s23/safety_items.jsonl --out reports/intent/run3/safety_pred.json
    python -m engine.intent.safety_eval evaluate --run run2=<safety_pred.json> --run run3=<safety_pred.json> --baseline run2 \
        --texts D:/ml-cache/s23/safety_items.jsonl --out reports/intent/run3/safety_eval.json

Unit of analysis: INDEPENDENT GROUPS. Test items that near-duplicate each other (Jaccard 0.9, `safety_split.neardup_clusters`)
form one group; the number of groups that contain a positive (131 before ledger exclusions) is the binding n. A group's recall
credit is the share of its positive members flagged, so a group counts once; precision weights each item by 1 / (labelled items in its
group). Wilson intervals use n = groups (recall) or the weighted flagged mass (precision); the bootstrap resamples GROUPS, not items.
Operating points: P = argmax of the validation-calibrated probabilities (primary); S = threshold on p(high) chosen on SAFETY
validation to maximise F1 of the high class (ties go to the higher threshold; 17 positives, so noisy and secondary).
No review text is written; text is read from the private items file.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from engine.labelling.repilot2_report import wilson
from engine.labelling.safety_split import fingerprint, neardup_clusters

SEED = 42
HIGH = "high"


def f1_threshold(p_high: np.ndarray, y: np.ndarray) -> float:
    """Threshold on p(high) maximising F1 of the high class on validation; ties -> the higher threshold."""
    best, thr = -1.0, 0.5
    for t in sorted(set(p_high.tolist())):
        flag = p_high >= t
        tp, fp, fn = (flag & y).sum(), (flag & ~y).sum(), (~flag & y).sum()
        f1 = 2 * tp / (2 * tp + fp + fn) if tp else 0.0
        if f1 >= best:
            best, thr = f1, float(t)
    return thr


def aggregates(y: np.ndarray, flag: np.ndarray, g: np.ndarray) -> dict[str, np.ndarray]:
    """Per-group sums: has_pos, hit (share of positives flagged), fl_w and tp_w (1/size weighted flagged, flagged-and-true)."""
    _, inv = np.unique(g, return_inverse=True)
    size = np.bincount(inv).astype(float)
    pos = np.bincount(inv, weights=y.astype(float))
    posflag = np.bincount(inv, weights=(y & flag).astype(float))
    return {
        "has_pos": (pos > 0).astype(float), "hit": np.where(pos > 0, posflag / np.maximum(pos, 1), 0.0),
        "fl_w": np.bincount(inv, weights=flag.astype(float)) / size,
        "tp_w": np.bincount(inv, weights=(flag & y).astype(float)) / size,
    }  # fmt: skip


def counts(n_groups: int, n_boot: int) -> np.ndarray:
    return np.random.default_rng(SEED).multinomial(
        n_groups, np.full(n_groups, 1.0 / n_groups), size=n_boot
    )


def _pct(v: np.ndarray) -> list[float]:
    return [round(float(x), 4) for x in np.nanpercentile(v, [2.5, 97.5])]


def boot(a: dict[str, np.ndarray], w: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    with np.errstate(invalid="ignore", divide="ignore"):
        return (w @ (a["hit"] * a["has_pos"])) / (w @ a["has_pos"]), (w @ a["tp_w"]) / (
            w @ a["fl_w"]
        )


def point(a: dict[str, np.ndarray], y: np.ndarray, flag: np.ndarray, w: np.ndarray) -> dict:
    n_pos, hit = int(a["has_pos"].sum()), float((a["hit"] * a["has_pos"]).sum())
    fl, tp = float(a["fl_w"].sum()), float(a["tp_w"].sum())
    rec_b, prec_b = boot(a, w)
    return {
        "independent_positive_groups": n_pos, "recall": round(hit / n_pos, 4),
        "recall_wilson95": list(wilson(round(hit), n_pos)), "recall_group_boot95": _pct(rec_b),
        "precision": round(tp / fl, 4) if fl else None,
        "precision_wilson95": list(wilson(round(tp), round(fl))) if fl else None,
        "precision_group_boot95": _pct(prec_b) if fl else None,
        "nominal_items_shown_beside": {  # NOT the binding n: near-duplicates are counted individually here
            "positive_items": int(y.sum()), "flagged_items": int(flag.sum()), "true_positive_items": int((y & flag).sum()),
        },
    }  # fmt: skip


def load_pred(path: Path) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    d["p_high"] = {
        s: np.array(d["splits"][s]["probs"]).reshape(len(d["splits"][s]["ids"]), -1)[:, d["classes"].index(HIGH)]
        for s in ("val", "test")
    }  # fmt: skip
    d["argmax_high"] = {
        s: np.array(d["splits"][s]["probs"]).reshape(len(d["splits"][s]["ids"]), -1).argmax(1) == d["classes"].index(HIGH)
        for s in ("val", "test")
    }  # fmt: skip
    return d


def evaluate(
    preds: dict[str, dict], baseline: str, urgency: dict[str, str | None], shas: dict[str, str],
    groups_of: dict[str, int], excluded: set[str], n_boot: int = 1000,
) -> dict:  # fmt: skip
    """preds[name] from `load_pred`; urgency = panel consensus per safety id (None = abstained)."""
    ref = next(iter(preds.values()))
    ids = ref["splits"]["test"]["ids"]
    keep = [j for j, i in enumerate(ids) if urgency[i] is not None and shas[i] not in excluded]
    t_ids = [ids[j] for j in keep]
    y = np.array([urgency[i] == HIGH for i in t_ids])
    g = np.array([groups_of[i] for i in t_ids])
    vids = ref["splits"]["val"]["ids"]
    vkeep = [j for j, i in enumerate(vids) if urgency[i] is not None]
    yv = np.array([urgency[vids[j]] == HIGH for j in vkeep])
    w = counts(len(np.unique(g)), n_boot)
    out: dict = {
        "label_basis": "enriched, keyword-mined, silver (panel) labels; not a base-rate number; never pooled with the Amendment 5 sealed set",
        "bootstrap": f"{n_boot} resamples of independent near-duplicate GROUPS, seed {SEED}; no product ids exist for this stratum",
        "n_test_items": len(ids), "n_test_items_labelled_urgency": sum(urgency[i] is not None for i in ids),
        "n_ledger_excluded_dropped": sum(shas[i] in excluded for i in ids),
        "n_test_items_evaluated": len(t_ids), "n_val_items_evaluated": len(vkeep), "n_val_high": int(yv.sum()),
        "runs": {},
    }  # fmt: skip
    agg: dict[str, dict] = {}
    for name, p in preds.items():
        assert p["splits"]["test"]["ids"] == ids, (
            "prediction files must cover the same safety test ids in the same order"
        )
        thr = f1_threshold(p["p_high"]["val"][vkeep], yv)
        flags = {
            "P_argmax": p["argmax_high"]["test"][keep],
            "S_val_f1_threshold": p["p_high"]["test"][keep] >= thr,
        }
        dthr = p.get(
            "decision_threshold"
        )  # serving default (Amendment 8): fitted on the Amendment 5 validation set
        if dthr is not None:
            flags["D_serving_default"] = p["p_high"]["test"][keep] >= dthr
        out["runs"][name] = {
            "temperature_urgency": p.get("temperature"),
            "variant": p.get("variant"),
            "S_threshold_on_safety_val": round(thr, 6),
            "D_threshold_on_base_val": dthr,
        }
        for op, flag in flags.items():
            a = aggregates(y, flag, g)
            agg[f"{name}/{op}"] = a
            out["runs"][name][op] = point(a, y, flag, w)
    out["independent_positive_groups"] = out["runs"][baseline]["P_argmax"][
        "independent_positive_groups"
    ]
    out["paired_vs_baseline"] = {"baseline": baseline}
    for name in preds:
        if name == baseline:
            continue
        d = {}
        for op in ("P_argmax", "S_val_f1_threshold", "D_serving_default"):
            if op not in out["runs"][name] or op not in out["runs"][baseline]:
                continue
            (r0, p0), (r1, p1) = boot(agg[f"{baseline}/{op}"], w), boot(agg[f"{name}/{op}"], w)
            a0, a1 = out["runs"][baseline][op], out["runs"][name][op]
            d[op] = {
                "recall_diff": round(a1["recall"] - a0["recall"], 4), "recall_diff_group_boot95": _pct(r1 - r0),
                "precision_diff": None if a0["precision"] is None or a1["precision"] is None else round(a1["precision"] - a0["precision"], 4),
                "precision_diff_group_boot95": _pct(p1 - p0),
            }  # fmt: skip
        out["paired_vs_baseline"][name] = d
    return out


def predict(a: argparse.Namespace) -> None:
    import torch

    from engine.intent import quantize as Q
    from engine.intent import train as T
    from engine.labelling.leakage_ledger import safety_items

    labels = json.loads(Path(a.labels).read_text(encoding="utf-8"))
    sizes = {t: len(c) for t, c in labels["classes"].items()}
    meta = json.loads(Path(a.meta).read_text(encoding="utf-8"))
    train_meta = json.loads(Path(a.train_meta or a.meta).read_text(encoding="utf-8"))
    cfg = T.IntentConfig(**train_meta["config"])
    temp = (meta["variants"][a.variant]["temperatures"] if a.variant else meta["temperatures"])[
        "urgency"
    ]
    base, tok = Q.load_checkpoint(Path(a.weights), sizes)
    if a.variant:
        model, device = Q.quantize(base, a.variant), "cpu"
    else:  # the GPU fp16 model exactly as trained: frozen fp16 word embeddings, fp16 autocast
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = base
        if device == "cuda":
            model.enc.get_input_embeddings().half()
        model.to(device)
    texts = {
        r["id"]: r["text"]
        for r in map(json.loads, Path(a.texts).read_text(encoding="utf-8").splitlines())
    }
    splits = {s: [r["id"] for r in safety_items() if r["split"] == s] for s in ("val", "test")}
    classes = list(labels["classes"]["urgency"])
    dec = (meta["variants"][a.variant] if a.variant else meta).get("decision", {})
    if isinstance(dec, dict) and "thresholds" in dec:  # train metadata nests under "thresholds"
        dec = dec["thresholds"]
    out: dict = {
        "task": "urgency",
        "classes": classes,
        "temperature": temp,
        "decision_threshold": dec.get("urgency", {}).get(HIGH, {}).get("threshold"),
        "variant": a.variant or f"gpu_fp16:{device}",
        "splits": {},
    }
    for s, ids in splits.items():
        lg = T.infer(model, tok, [texts[i] for i in ids], cfg, device)["urgency"]
        z = lg / (temp or 1.0)
        pr = np.exp(z - z.max(1, keepdims=True))
        out["splits"][s] = {
            "ids": ids,
            "probs": np.round(pr / pr.sum(1, keepdims=True), 6).tolist(),
        }
    Path(a.out).write_text(json.dumps(out) + "\n", encoding="utf-8")
    print(f"wrote {a.out}")


def load_context(labels: str, texts: str, ledger: str) -> dict:
    """Everything `evaluate` needs besides the predictions: panel urgency, content hashes, near-duplicate groups, exclusions."""
    from engine.labelling.leakage_ledger import safety_items

    rows = {r["id"]: r for r in safety_items()}
    lab = {
        r["id"]: r for r in map(json.loads, Path(labels).read_text(encoding="utf-8").splitlines())
    }
    text = {
        r["id"]: r["text"]
        for r in map(json.loads, Path(texts).read_text(encoding="utf-8").splitlines())
    }
    test_ids = [i for i, r in rows.items() if r["split"] == "test"]
    return {
        "urgency": {i: lab[i]["urgency"] for i in rows}, "shas": {i: rows[i]["sha"] for i in rows},
        "groups": neardup_clusters({i: fingerprint(text[i]) for i in test_ids}),
        "excluded": set(json.loads(Path(ledger).read_text(encoding="utf-8")).get("sealed_excluded", [])),
        "test_ids": test_ids, "lab": lab,
    }  # fmt: skip


def run_evaluate(a: argparse.Namespace) -> None:
    c = load_context(a.labels, a.texts, a.ledger)
    cl, lab, test_ids = c["groups"], c["lab"], c["test_ids"]
    preds = {}
    for kv in a.run:
        name, path = kv.split("=", 1)
        preds[name] = load_pred(Path(path))
    res = evaluate(
        preds, a.baseline, c["urgency"], c["shas"], cl, c["excluded"], a.n_boot,
    )  # fmt: skip
    res["independent_positive_groups_before_ledger_exclusions"] = len(
        {cl[i] for i in test_ids if lab[i]["urgency"] == HIGH}
    )
    res["nominal_positive_items_before_ledger_exclusions"] = sum(
        lab[i]["urgency"] == HIGH for i in test_ids
    )
    Path(a.out).write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {a.out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("predict")
    p.add_argument("--weights", required=True)
    p.add_argument(
        "--meta",
        required=True,
        help="metadata.json (GPU) or the quantize metadata.json with --variant",
    )
    p.add_argument("--variant")
    p.add_argument("--train-meta", help="train metadata.json (config); needed with --variant")
    p.add_argument(
        "--labels", required=True, help="reports/intent/labels/labels.json (class lists)"
    )
    p.add_argument("--texts", required=True)
    p.add_argument("--out", required=True)
    e = sub.add_parser("evaluate")
    e.add_argument(
        "--run", action="append", required=True, help="name=safety_pred.json, repeatable"
    )
    e.add_argument("--baseline", required=True)
    e.add_argument("--texts", required=True)
    e.add_argument("--labels", default="reports/labelling/safety/labels.jsonl")
    e.add_argument("--ledger", default="reports/labelling/ledger.json")
    e.add_argument("--n-boot", type=int, default=1000)
    e.add_argument("--out", required=True)
    a = ap.parse_args()
    predict(a) if a.cmd == "predict" else run_evaluate(a)


if __name__ == "__main__":
    main()
