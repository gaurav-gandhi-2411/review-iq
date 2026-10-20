"""Post-deploy check for the PRIVATE classify service: batch-1 sealed-set parity against the live URL (S26, reworked S27).

    CLASSIFY_ID_TOKEN=$(gcloud auth print-identity-token --account=<invoker>) PYTHONPATH=. \
        python scripts/classify_live_parity.py --url <url> --texts D:/ml-cache/review-intent/scale.jsonl --out <result.json> \
        [--limit N]

Sends every sealed review ALONE (one review per request, the way the review-iq service would) and compares each served decision
(class or 'abstained') with the offline reference: the same pred files and decision rule as
`scripts/classify_parity.py --backend onnx` (expected: 0 mismatches, the S25 result). Also checks the service reports the
committed thresholds file's sha256 and records client-side latency per request (p50/p95; includes the network path).
Authentication is the Cloud Run identity token only (no API key exists on this service): CLASSIFY_ID_TOKEN is read from the
environment, sent as `Authorization: Bearer`, and never printed or written. Without it no header is sent (a local container).
Review text is read from the private texts file and sent over TLS; it is never written. Exit 1 on any mismatch or error.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

import httpx
from app.core.classify import runtime

from scripts import classify_parity as cp

ONNX = cp.CFGS["onnx"]


def _pct(v: list[float], q: float) -> float:
    s = sorted(v)
    return round(s[min(len(s) - 1, int(q * len(s)))], 1)


def run(url: str, texts_path: Path, limit: int | None, timeout: float) -> dict:
    from engine.intent.labels import load_texts
    from engine.intent.llm_moat import sealed_ids

    cp.CFG.update(ONNX)
    th = runtime.load_thresholds(ONNX["thresholds"])
    ids, off = cp.offline_decisions(th)
    sealed = set(sealed_ids())
    texts_by_id = load_texts(texts_path)
    keep = [i for i, x in enumerate(ids) if x in sealed][:limit]
    headers = {"Content-Type": "application/json"}
    if os.environ.get("CLASSIFY_ID_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["CLASSIFY_ID_TOKEN"]
    mism: list[dict] = []
    lat: list[float] = []
    errors: list[dict] = []
    sha_seen: set[str] = set()
    with httpx.Client(timeout=timeout, headers=headers) as c:
        for i in keep:
            t0 = time.perf_counter()
            try:
                r = c.post(
                    f"{url.rstrip('/')}/v2/classify",
                    json={"reviews": [{"id": ids[i], "text": texts_by_id[ids[i]]}]},
                )
            except httpx.HTTPError as e:
                errors.append({"id": ids[i], "error": type(e).__name__})
                continue
            lat.append((time.perf_counter() - t0) * 1000)
            if r.status_code != 200:
                errors.append({"id": ids[i], "status": r.status_code, "code": _code(r)})
                continue
            d = r.json()
            sha_seen.add(d["decision"]["thresholds_sha256"])
            for t in cp.TASKS:
                served = d["results"][0]["tasks"][t]["label"]
                if served != off[t]["labels"][i]:
                    mism.append(
                        {"id": ids[i], "task": t, "served": served, "offline": off[t]["labels"][i]}
                    )
    return {
        "url_host": url.split("//")[-1].split("/")[0],
        "n_items": len(keep),
        "n_task_decisions": len(keep) * len(cp.TASKS),
        "n_requests_ok": len(keep) - len(errors),
        "n_errors": len(errors),
        "errors": errors[:20],
        "n_mismatches": len(mism),
        "mismatches": mism,
        "thresholds_sha256_expected": th.sha256,
        "thresholds_sha256_served": sorted(sha_seen),
        "thresholds_match": sha_seen == {th.sha256},
        "client_latency_ms": {
            "n": len(lat),
            "p50": round(statistics.median(lat), 1) if lat else None,
            "p95": _pct(lat, 0.95) if lat else None,
            "max": round(max(lat), 1) if lat else None,
        },
    }


def _code(r: httpx.Response) -> str | None:
    try:
        return r.json().get("code")
    except ValueError:
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--texts", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--timeout", type=float, default=60.0)
    a = ap.parse_args()
    res = run(a.url, Path(a.texts), a.limit, a.timeout)
    Path(a.out).write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    print(
        {k: res[k] for k in ("n_items", "n_errors", "n_mismatches", "thresholds_match")},
        res["client_latency_ms"],
    )
    sys.exit(
        0 if res["n_mismatches"] == 0 and res["n_errors"] == 0 and res["thresholds_match"] else 1
    )


if __name__ == "__main__":
    main()
