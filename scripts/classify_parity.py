"""Parity check: the SERVED code path (app.core.classify.runtime) against the OFFLINE evaluation artifacts, on the sealed set.

    PYTHONPATH=. python scripts/classify_parity.py --artifacts D:/ml-cache/s24/classify_artifacts \
        --texts D:/ml-cache/review-intent/scale.jsonl --out <result.json>

Offline reference: `reports/intent/run2_cpu/int8_ffn_full/pred_<task>.json` (calibrated probabilities of the int8 variant) and
the thresholds recomputed from them with the evaluator's own functions (`scripts/build_classify_thresholds.py`).
Checks: (1) the committed thresholds file equals that recomputation exactly (python float equality, i.e. bit-for-bit);
(2) for every sealed item and task, the served decision (class or 'abstained') equals the offline decision
(`decide` + conservative abstention on the offline probabilities); (3) max |served - offline| probability.
Torch dynamic int8 quantises ACTIVATIONS per tensor, so a logit depends on the other items in its batch. The offline
probabilities came from batches of 128 in pred-file order; `offline_batches` reproduces exactly that (the code-path
parity: expected 0 mismatches), `served_per_item` is what the endpoint does (one review per forward, so a result never
depends on the rest of the request) and `batch_8` is context. All are reported. Review text is read from the private
scale.jsonl and never written.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from app.core.classify import runtime
from engine.intent.decide import decide

from scripts.build_classify_thresholds import TASKS, build

ROOT = Path(__file__).resolve().parents[1]
CFGS = {
    "torch": {"pred": ROOT / "reports/intent/run2_cpu/int8_ffn_full", "eval": ROOT / "reports/intent/eval_run2_int8_ffn_full.json", "variant": "int8_ffn_full", "thresholds": runtime.THRESHOLDS_PATH},
    "onnx": {"pred": ROOT / "reports/intent/run2_cpu/onnx_static_int8_x64", "eval": ROOT / "reports/intent/eval_run2_onnx_static_int8_x64.json", "variant": "onnx_static_int8_x64", "thresholds": runtime.THRESHOLDS_ONNX_PATH},
    "onnx_s25": {"pred": ROOT / "reports/intent/run2_cpu/onnx_static_int8", "eval": ROOT / "reports/intent/eval_run2_onnx_static_int8.json", "variant": "onnx_static_int8", "thresholds": runtime.THRESHOLDS_ONNX_S25_PATH},
}  # fmt: skip
CFG = dict(CFGS["torch"])  # main() switches it with --backend

LABELS = ROOT / "reports/intent/labels/labels.json"
OFFLINE_BATCH = 128  # engine.intent.train.infer


def thresholds_bit_for_bit() -> dict:
    """Committed thresholds file vs a fresh recomputation from the offline artifacts (exact equality of every value)."""
    committed = json.loads(CFG["thresholds"].read_text(encoding="utf-8"))
    fresh = json.loads(
        json.dumps(build(CFG["pred"], LABELS, CFG["eval"], CFG["variant"]))
    )  # round-trip: same float repr as the file
    return {
        "equal": committed == fresh,
        "committed_sha256": runtime.load_thresholds(CFG["thresholds"]).sha256,
    }


def offline_decisions(th: runtime.Thresholds) -> tuple[list[str], dict[str, dict]]:
    """-> (test ids in pred-file order, task -> {probs, label list}) from the offline pred files."""
    out, ids = {}, None
    for t in TASKS:
        d = json.loads((CFG["pred"] / f"pred_{t}.json").read_text(encoding="utf-8"))
        ids = d["splits"]["test"]["ids"]
        spec = th.tasks[t]
        p = np.array(d["splits"]["test"]["probs"]).reshape(len(ids), len(spec.classes))
        cand = decide(p, spec.classes, spec.rare)
        lab = [
            runtime.ABSTAINED if p[i].max() < spec.abstain_below else spec.classes[cand[i]]
            for i in range(len(ids))
        ]
        out[t] = {"probs": p, "labels": lab}
    return ids, out


def compare(
    th: runtime.Thresholds,
    logits: dict[str, np.ndarray],
    ids: list[str],
    sealed: set[str],
    off: dict[str, dict],
) -> dict:
    """Decision mismatches (ids and labels only) and max probability difference over the sealed items."""
    served = runtime.decisions(th, logits)
    keep = [i for i, x in enumerate(ids) if x in sealed]
    mism, max_diff = [], 0.0
    for t in TASKS:
        cls = th.tasks[t].classes
        for i in keep:
            sv = served[i][t]
            max_diff = max(
                max_diff,
                max(
                    abs(sv["probabilities"][c] - float(off[t]["probs"][i][j]))
                    for j, c in enumerate(cls)
                ),
            )
            if sv["label"] != off[t]["labels"][i]:
                mism.append(
                    {"id": ids[i], "task": t, "served": sv["label"], "offline": off[t]["labels"][i]}
                )
    return {
        "n_items": len(keep), "n_task_decisions": len(keep) * len(TASKS), "n_mismatches": len(mism),
        "mismatches_by_task": {t: sum(m["task"] == t for m in mism) for t in TASKS},
        "mismatch_kinds": {
            "abstain_vs_answer": sum(runtime.ABSTAINED in (m["served"], m["offline"]) for m in mism),
            "different_class": sum(runtime.ABSTAINED not in (m["served"], m["offline"]) for m in mism),
        },
        "mismatches": mism, "max_abs_prob_diff": max_diff,
        "vs_consensus_labels": vs_gold({t: [served[i][t]["label"] for i in range(len(ids))] for t in TASKS}, ids, keep),
    }  # fmt: skip


def vs_gold(labels: dict[str, list[str]], ids: list[str], keep: list[int]) -> dict:
    """Per task, on sealed items with a consensus label: coverage (not abstained) and precision among the answered."""
    gold = json.loads(LABELS.read_text(encoding="utf-8"))["labels"]["test"]
    out = {}
    for t in TASKS:
        lab = [(labels[t][i], gold[t][ids[i]]) for i in keep if ids[i] in gold[t]]
        ans = [(p, g) for p, g in lab if p != runtime.ABSTAINED]
        out[t] = {
            "n_labelled": len(lab), "coverage": round(len(ans) / len(lab), 4),
            "precision_answered": round(sum(p == g for p, g in ans) / max(len(ans), 1), 4),
        }  # fmt: skip
    return out


def run(artifacts: Path, texts_path: Path) -> dict:
    from engine.intent.labels import load_texts
    from engine.intent.llm_moat import sealed_ids

    th = runtime.load_thresholds(CFG["thresholds"])
    onnx = CFG["variant"].startswith("onnx_static_int8")
    backend = (
        runtime.OnnxBackend(artifacts, th) if onnx else runtime.TorchInt8Backend(artifacts, th)
    )
    ids, off = offline_decisions(th)
    sealed = set(sealed_ids())
    texts_by_id = load_texts(texts_path)
    texts = [texts_by_id[i] for i in ids]
    res: dict = {
        "thresholds_bit_for_bit": thresholds_bit_for_bit(), "n_sealed_ids": len(sealed),
        "n_pred_test_items": len(ids), "n_sealed_in_pred": sum(i in sealed for i in ids), "modes": {},
    }  # fmt: skip
    keep = [i for i, x in enumerate(ids) if x in sealed]
    res["offline_reference_vs_consensus_labels"] = vs_gold(
        {t: off[t]["labels"] for t in TASKS}, ids, keep
    )
    # offline_batches: batches of 128 in pred-file order, as the offline evaluation ran. served_per_item: what the endpoint
    # does. batch_8: context only (an API caller never gets it).
    runs = {
        "offline_batches": lambda: backend.logits_batched(texts, OFFLINE_BATCH),
        "served_per_item": lambda: backend.logits(texts),
        "batch_8": lambda: backend.logits_batched(texts, 8),
    }
    if onnx:
        # The ONNX reference (the pred files) is itself scored one review per forward with the transformers tokenizer, so
        # `served_per_item` is the implementation parity (tokenizers library, runtime.py) and the padded batches test batch
        # independence, the property the dynamic variant lacked.
        runs = {
            "served_per_item": runs["served_per_item"],
            "padded_batches_128": runs["offline_batches"],
            "batch_8": runs["batch_8"],
        }
        res["tokenization"] = tokenization_check(artifacts, backend, texts)
    for name, fn in runs.items():
        res["modes"][name] = compare(th, fn(), ids, sealed, off)
    return res


def tokenization_check(artifacts: Path, backend: runtime.OnnxBackend, texts: list[str]) -> dict:
    """Token ids from the `tokenizers` library (served) against the transformers tokenizer (offline), item by item."""
    try:
        from transformers import AutoTokenizer
    except ImportError:
        return {"checked": False, "reason": "transformers not installed"}
    tok = AutoTokenizer.from_pretrained(artifacts / "hf")
    bad = [
        i
        for i, t in enumerate(texts)
        if backend.token_ids(t) != tok(t, truncation=True, max_length=128)["input_ids"]
    ]
    return {"checked": True, "n_texts": len(texts), "n_different": len(bad)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts", required=True)
    ap.add_argument("--texts", required=True)
    ap.add_argument("--out")
    ap.add_argument("--backend", choices=sorted(CFGS), default="torch")
    a = ap.parse_args()
    CFG.update(CFGS[a.backend])
    res = run(Path(a.artifacts), Path(a.texts))
    for k, v in res["modes"].items():
        print(k, {x: v[x] for x in ("n_items", "n_mismatches", "max_abs_prob_diff")})
    print("thresholds bit-for-bit:", res["thresholds_bit_for_bit"])
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
