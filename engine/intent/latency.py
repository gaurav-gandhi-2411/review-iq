"""CPU latency of the review-intent model, fp32 vs int8 (S23).

    python -m engine.intent.latency --weights D:/ml-cache/s23/run2/model.pt --labels reports/intent/labels/labels.json \
        --texts D:/ml-cache/review-intent/scale.jsonl --manifest reports/labelling/scale/manifest.json \
        --ledger reports/labelling/ledger.json --out reports/intent/latency_run2.json [--threads N]

Inputs are real sealed-set review texts (ledger-excluded items removed), in a seeded random order, never the same
batch twice in a row. Each configuration gets WARMUP calls that are discarded, then CALLS timed calls; a call is
tokenise (padding to the longest in the batch, truncation 128) plus one forward pass through the encoder and all task
heads. Tokenisation and forward are also reported separately. Latency is wall-clock `perf_counter`, so any other
process on the machine contends with it; the result records the CPU model, thread count and system CPU use before and
after so a reader can judge that. No review text is written.
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import subprocess
import time
from pathlib import Path

import numpy as np
import torch

from engine.intent import quantize as Q
from engine.intent.labels import load_texts

WARMUP, CALLS, MAX_LEN, SEED = 20, 200, 128, 42


def cpu_info() -> dict:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors | ConvertTo-Json"],
            capture_output=True, text=True, check=True, timeout=30,
        )  # fmt: skip
        d = json.loads(r.stdout)
        return d[0] if isinstance(d, list) else d
    except Exception:  # noqa: BLE001
        return {"Name": platform.processor() or "unknown"}


def pct(v: list[float]) -> dict:
    a = np.array(v)
    return {f"p{q}": round(float(np.percentile(a, q)), 2) for q in (50, 95, 99)} | {
        "mean": round(float(a.mean()), 2)
    }


@torch.no_grad()
def bench(model: torch.nn.Module, tok: object, batches: list[list[str]]) -> dict:
    tot, tk, fw = [], [], []
    for i, b in enumerate(batches):
        t0 = time.perf_counter()
        enc = tok(b, padding=True, truncation=True, max_length=MAX_LEN, return_tensors="pt")
        t1 = time.perf_counter()
        model(enc["input_ids"], enc["attention_mask"])
        t2 = time.perf_counter()
        if i >= WARMUP:
            tot.append((t2 - t0) * 1e3)
            tk.append((t1 - t0) * 1e3)
            fw.append((t2 - t1) * 1e3)
    return {"calls": len(tot), "total_ms": pct(tot), "tokenise_ms": pct(tk), "forward_ms": pct(fw)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--texts", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--threads", type=int, default=0, help="0 = torch default")
    ap.add_argument("--variants", nargs="+", default=list(Q.VARIANTS))
    a = ap.parse_args()
    if a.threads:
        torch.set_num_threads(a.threads)
    import psutil

    man = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    excluded = set(
        json.loads(Path(a.ledger).read_text(encoding="utf-8")).get("sealed_excluded", [])
    )
    ids = [r["id"] for r in man["items"] if r["split"] == "test" and r["sha"] not in excluded]
    texts = load_texts(Path(a.texts))
    pool = [texts[i] for i in ids]
    rng = random.Random(SEED)
    lab = json.loads(Path(a.labels).read_text(encoding="utf-8"))
    base, tok = Q.load_checkpoint(Path(a.weights), {t: len(c) for t, c in lab["classes"].items()})
    lens = [len(tok(t, truncation=True, max_length=MAX_LEN)["input_ids"]) for t in pool]
    out: dict = {
        "cpu": cpu_info(), "torch_threads": torch.get_num_threads(), "torch": torch.__version__,
        "os": platform.platform(), "n_sealed_texts": len(pool), "max_len": MAX_LEN,
        "warmup_calls": WARMUP, "seed": SEED,
        "tokens_per_text": {"mean": round(float(np.mean(lens)), 1), "p95": float(np.percentile(lens, 95)),
                            "share_truncated_at_128": round(float(np.mean([n >= MAX_LEN for n in lens])), 3)},
        "cpu_percent_before": psutil.cpu_percent(interval=1.0), "variants": {},
    }  # fmt: skip
    plans = {1: [[rng.choice(pool)] for _ in range(WARMUP + CALLS)],
             32: [rng.sample(pool, 32) for _ in range(WARMUP + CALLS)]}  # fmt: skip
    for v in a.variants:
        model = Q.quantize(base, v)
        out["variants"][v] = {}
        for bs, batches in plans.items():
            out["variants"][v][f"batch_{bs}"] = bench(model, tok, batches)
            print(v, bs, out["variants"][v][f"batch_{bs}"]["total_ms"], flush=True)
    out["cpu_percent_after"] = psutil.cpu_percent(interval=1.0)
    Path(a.out).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
