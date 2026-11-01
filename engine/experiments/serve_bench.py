"""E3 benchmark of an exported artifact: int8 quality, single-text latency, memory.

    python -m engine.experiments.serve_bench --artifact DIR --out out.json [--recalibrate]

Latency is wall time of Predictor.predict(text) for one short text on N intra-op threads (default
1, matching a 1-vCPU Cloud Run instance) after a warm-up. Quality is on the CLINC150 test split and
its out-of-scope test set, using the artifact's own threshold (or one re-derived on validation with
--recalibrate, on a copy so the artifact is untouched).
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import tempfile
import time
from pathlib import Path

import numpy as np

from engine import data as D
from engine import metrics as M
from engine.export import calibrate_threshold
from engine.serve import Predictor


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--n-latency", type=int, default=300)
    ap.add_argument("--recalibrate", action="store_true")
    a = ap.parse_args()

    sp = D.load_clinc()
    idx = {lab: i for i, lab in enumerate(sp.labels)}
    art = Path(a.artifact)
    if a.recalibrate:
        tmp = Path(tempfile.mkdtemp())
        shutil.copytree(art, tmp / "art")
        art = tmp / "art"
        calibrate_threshold(art, [e.text for e in sp.val])
    pred = Predictor(art, threads=a.threads)
    if pred.labels != sp.labels:
        raise SystemExit("artifact labels differ from CLINC150 label order")

    def run(rows: list[D.Example]) -> list:
        out = []
        for i in range(0, len(rows), 64):
            out += pred.predict_batch([e.text for e in rows[i : i + 64]])
        return out

    y = np.array([idx[e.label] for e in sp.test])
    got = run(sp.test)
    p = np.array([idx[g.label] for g in got])
    unk_known = np.array([g.is_unknown for g in got])
    unk_oos = np.array([g.is_unknown for g in run(sp.extra["oos_test"])])

    texts = [e.text for e in sp.test[:: max(1, len(sp.test) // a.n_latency)]][: a.n_latency]
    for t in texts[:15]:
        pred.predict(t)  # warm-up
    lat = []
    for t in texts:
        t0 = time.perf_counter()
        pred.predict(t)
        lat.append((time.perf_counter() - t0) * 1000)
    try:
        import psutil

        rss = round(psutil.Process().memory_info().rss / 1e6)
    except ImportError:
        rss = None
    res = {
        "artifact": str(a.artifact),
        "threads": a.threads,
        "recalibrated": a.recalibrate,
        "scorer": pred.scorer,
        "int8_macro_f1": round(M.macro_f1(y, p), 4),
        "int8_accuracy": round(M.accuracy(y, p), 4),
        "known_retention_test": round(float(1 - unk_known.mean()), 4),
        "oos_rejection_recall": round(float(unk_oos.mean()), 4),
        "latency_ms_p50": round(float(np.percentile(lat, 50)), 1),
        "latency_ms_p95": round(float(np.percentile(lat, 95)), 1),
        "latency_ms_p99": round(float(np.percentile(lat, 99)), 1),
        "n_latency": len(lat),
        "rss_mb": rss,
        "model_mb": round((Path(a.artifact) / "model.int8.onnx").stat().st_size / 1e6),
        "cpu": platform.processor() or platform.machine(),
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps(res))


if __name__ == "__main__":
    main()
