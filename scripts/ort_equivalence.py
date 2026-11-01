"""S30 numerics equivalence: the same 200 sealed reviews scored on three hosts and compared pairwise (label mismatches over the 4 served
tasks, and the per-review maximum probability difference).

    python scripts/ort_equivalence.py items   --texts <scale.jsonl> --out-dir D:/ml-cache/s30/equiv        # writes items.jsonl (private)
    python scripts/ort_equivalence.py local   --artifacts <dir with model.onnx, hf/> --out-dir ...        # this machine, pinned and unpinned
    python scripts/ort_equivalence.py kaggle  --npz <kernel output logits.npz> --out-dir ...              # Kaggle CPU kernel output -> decisions
    python scripts/ort_equivalence.py cloudrun --url <service url> --out-dir ...                          # CLASSIFY_ID_TOKEN in the environment
    python scripts/ort_equivalence.py compare --out-dir ... [--hosts a,b,c]                               # pairwise table -> equivalence.json

Every host's output is reduced to the same shape (per task: probabilities and the served label under ONE thresholds file), so logits
from a kernel and probabilities from the HTTP API compare directly. Review text is read from the private texts file and sent only to the
private service; nothing here writes it. The token (if any) is read from the environment and never printed.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
from pathlib import Path

import numpy as np
from app.core.classify import runtime

TASKS = ("needs_action", "broad_intent", "sentiment", "urgency")
N_ITEMS = 200


def _items(out_dir: Path) -> list[dict]:
    return [
        json.loads(x)
        for x in (out_dir / "items.jsonl").read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]


def _reduce(
    th: runtime.Thresholds, logits: dict[str, np.ndarray]
) -> dict[str, dict[str, np.ndarray | list[str]]]:
    dec = runtime.decisions(th, logits)
    out: dict = {}
    for t in TASKS:
        cl = th.tasks[t].classes
        out[t] = {
            "probs": np.array(
                [[d[t]["probabilities"][c] for c in cl] for d in dec], dtype=np.float64
            ),
            "labels": [d[t]["label"] for d in dec],
        }
    return out


def _save(out_dir: Path, host: str, red: dict) -> None:
    np.savez(out_dir / f"decisions_{host}.npz", **{f"{t}/probs": red[t]["probs"] for t in TASKS},
             **{f"{t}/labels": np.array(red[t]["labels"]) for t in TASKS})  # fmt: skip


def _load(out_dir: Path, host: str) -> dict:
    z = np.load(out_dir / f"decisions_{host}.npz")
    return {t: {"probs": z[f"{t}/probs"], "labels": list(z[f"{t}/labels"])} for t in TASKS}


def cmd_items(a: argparse.Namespace) -> None:
    from engine.intent.labels import load_texts
    from engine.intent.llm_moat import sealed_ids

    from scripts import classify_parity as cp

    cp.CFG.update(cp.CFGS["onnx"])
    th = runtime.load_thresholds(runtime.THRESHOLDS_ONNX_PATH)
    ids, _ = cp.offline_decisions(th)
    sealed, by_id = set(sealed_ids()), load_texts(a.texts)
    keep = [i for i in ids if i in sealed][:N_ITEMS]
    a.out_dir.mkdir(parents=True, exist_ok=True)
    (a.out_dir / "items.jsonl").write_text(
        "".join(json.dumps({"id": i, "text": by_id[i]}) + "\n" for i in keep), encoding="utf-8"
    )
    print("items", len(keep))


def cmd_local(a: argparse.Namespace) -> None:
    import onnxruntime as ort

    th = runtime.load_thresholds(a.thresholds)
    texts = [x["text"] for x in _items(a.out_dir)]
    pinned = runtime.OnnxBackend(a.artifacts, th, threads=2)  # pinned by construction
    _save(a.out_dir, "laptop_pinned", _reduce(th, pinned.logits(texts)))
    so = ort.SessionOptions()  # deliberately NOT through apply_numerics_pin: the S29 baseline
    so.intra_op_num_threads, so.inter_op_num_threads = 2, 1
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    pinned._sess = ort.InferenceSession(
        str(a.artifacts / "model.onnx"), so, providers=["CPUExecutionProvider"]
    )  # noqa: SLF001
    _save(a.out_dir, "laptop_unpinned", _reduce(th, pinned.logits(texts)))
    print("saved laptop_pinned, laptop_unpinned")


def cmd_kaggle(a: argparse.Namespace) -> None:
    th = runtime.load_thresholds(a.thresholds)
    z = np.load(a.npz)
    ids = [x["id"] for x in _items(a.out_dir)]
    assert list(z["ids"]) == ids, "kernel output is for different items"
    for name in ("pinned", "unpinned"):
        lg, i = z[name], 0
        logits = {}
        for t in TASKS:
            n = len(th.tasks[t].classes)
            logits[t] = lg[:, i : i + n]
            i += n
        _save(a.out_dir, f"kaggle_{name}", _reduce(th, logits))
    print("saved kaggle_pinned, kaggle_unpinned")


def cmd_cloudrun(a: argparse.Namespace) -> None:
    import httpx

    th = runtime.load_thresholds(a.thresholds)
    hdr = {"Authorization": "Bearer " + os.environ["CLASSIFY_ID_TOKEN"]}
    probs: dict[str, list] = {t: [] for t in TASKS}
    labels: dict[str, list] = {t: [] for t in TASKS}
    with httpx.Client(timeout=60, headers=hdr) as c:
        for it in _items(a.out_dir):
            r = c.post(
                f"{a.url.rstrip('/')}/v2/classify",
                json={"reviews": [{"id": it["id"], "text": it["text"]}]},
            )
            r.raise_for_status()
            tk = r.json()["results"][0]["tasks"]
            for t in TASKS:
                probs[t].append([tk[t]["probabilities"][cl] for cl in th.tasks[t].classes])
                labels[t].append(tk[t]["label"])
    _save(a.out_dir, a.host, {t: {"probs": np.array(probs[t]), "labels": labels[t]} for t in TASKS})
    print("saved", a.host)


def compare_pair(x: dict, y: dict) -> dict:
    mism = sum(
        int(a != b) for t in TASKS for a, b in zip(x[t]["labels"], y[t]["labels"], strict=True)
    )
    n = sum(len(x[t]["labels"]) for t in TASKS)
    per_review = np.max([np.abs(x[t]["probs"] - y[t]["probs"]).max(axis=1) for t in TASKS], axis=0)
    return {"label_mismatches": mism, "n_decisions": n, "max_abs_prob_diff": round(float(per_review.max()), 6),
            "median_per_review_max_diff": round(float(np.median(per_review)), 6), "p95_per_review_max_diff": round(float(np.percentile(per_review, 95)), 6)}  # fmt: skip


def cmd_compare(a: argparse.Namespace) -> None:
    hosts = a.hosts.split(",")
    data = {h: _load(a.out_dir, h) for h in hosts}
    res = {
        f"{h1} vs {h2}": compare_pair(data[h1], data[h2])
        for h1, h2 in itertools.combinations(hosts, 2)
    }
    (a.out_dir / "equivalence.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    for k, v in res.items():
        print(f"{k:42s} mismatches {v['label_mismatches']:>3d}/{v['n_decisions']}  max|dp| {v['max_abs_prob_diff']:<9}  median {v['median_per_review_max_diff']:<9}  p95 {v['p95_per_review_max_diff']}")  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (
        ("items", cmd_items),
        ("local", cmd_local),
        ("kaggle", cmd_kaggle),
        ("cloudrun", cmd_cloudrun),
        ("compare", cmd_compare),
    ):
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        p.add_argument("--out-dir", type=Path, required=True)
        p.add_argument("--thresholds", type=Path, default=runtime.THRESHOLDS_ONNX_PATH)
    sub.choices["items"].add_argument("--texts", type=Path, required=True)
    sub.choices["local"].add_argument("--artifacts", type=Path, required=True)
    sub.choices["kaggle"].add_argument("--npz", type=Path, required=True)
    sub.choices["cloudrun"].add_argument("--url", required=True)
    sub.choices["cloudrun"].add_argument("--host", default="cloudrun")
    sub.choices["compare"].add_argument("--hosts", required=True)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
