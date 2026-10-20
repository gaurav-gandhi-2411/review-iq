"""Amendment 16 capacity probe and cold-start sampler for a PRIVATE classify service (identity token in CLASSIFY_ID_TOKEN, never printed).

    python scripts/classify_capacity_probe.py probe --url <url> --length 256 --n 50 --texts D:/ml-cache/review-intent/scale.jsonl \
        --tokenizer D:/ml-cache/s25/classify_artifacts_onnx/hf/tokenizer.json --service review-iq-classify-probe --out probe_256.json
    python scripts/classify_capacity_probe.py cold --url <url> --service review-iq-classify --samples 3 --out cold.json

`probe`: sends N requests, one per request, each a text built from real reviews that fills EXACTLY `length` tokens under the served tokenizer (so the service
truncates at the budget and does the full amount of work), checks the response reports that length, and reads the server-side p50/p95 request latency and the
container memory p99 for the window from Cloud Monitoring (the registered budget: p95 < 1,000 ms and memory p99 < 0.85 of 2 GiB). `cold`: waits until the
service has no active or idle instance, sends ONE request and times it, repeated SAMPLES times. Review text is read from the private file and sent only to the
private service; nothing here writes it. gcloud is called with the explicit account.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

ACCOUNT = "gaurav.gandhi1129@gmail.com"
PROJECT = "reviewiq-prod-260813"
P95_BUDGET_MS = 1000.0
MEMORY_BUDGET = 0.85


def gcloud_token() -> str:
    return subprocess.run(
        ["gcloud", "auth", "print-access-token", f"--account={ACCOUNT}"],
        capture_output=True,
        text=True,
        shell=True,
        check=True,
    ).stdout.strip()


def monitoring(
    service: str,
    metric: str,
    aligner: str,
    start: datetime,
    end: datetime,
    period: int,
    extra: str = "",
) -> list[float]:
    flt = f'metric.type="{metric}" AND resource.labels.service_name="{service}"{extra}'
    params = {
        "filter": flt, "interval.startTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "interval.endTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "aggregation.alignmentPeriod": f"{period}s", "aggregation.perSeriesAligner": aligner,
    }  # fmt: skip
    url = (
        f"https://monitoring.googleapis.com/v3/projects/{PROJECT}/timeSeries?"
        + urllib.parse.urlencode(params)
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {gcloud_token()}"})
    series = json.loads(urllib.request.urlopen(req, timeout=60).read()).get("timeSeries", [])  # noqa: S310
    out = []
    for s in series:
        for p in s.get("points", []):
            v = p["value"]
            out.append(
                v.get(
                    "doubleValue", v.get("int64Value", v.get("distributionValue", {}).get("mean"))
                )
            )
    return [float(x) for x in out if x is not None]


def texts_of_length(texts: list[str], tok, length: int, n: int) -> list[str]:  # noqa: ANN001
    """n distinct texts of >= `length` tokens, made by concatenating real reviews (trimmed on a word boundary to <= 4,900 chars)."""
    out, buf, k = [], "", 0
    while len(out) < n:
        buf = (buf + " " + texts[k % len(texts)]).strip()
        k += 1
        if len(tok.encode(buf).ids) >= length + 4:
            out.append(buf[:4900].rsplit(" ", 1)[0])
            buf = ""
    return out


def probe(a: argparse.Namespace) -> None:
    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(a.tokenizer))
    texts = [
        json.loads(x)["text"] for x in a.texts.read_text(encoding="utf-8").splitlines() if x.strip()
    ]
    batch = texts_of_length(texts, tok, a.length, a.n)
    hdr = {"Authorization": "Bearer " + os.environ["CLASSIFY_ID_TOKEN"]}
    lat, reported = [], set()
    t_start = datetime.now(UTC) - timedelta(seconds=30)
    with httpx.Client(timeout=120, headers=hdr) as c:
        for i, t in enumerate(batch):
            t0 = time.perf_counter()
            r = c.post(
                f"{a.url.rstrip('/')}/v2/classify", json={"reviews": [{"id": str(i), "text": t}]}
            )
            r.raise_for_status()
            lat.append((time.perf_counter() - t0) * 1000)
            reported.add(r.json()["model"]["max_tokens"])
    time.sleep(90)  # Cloud Monitoring lags a minute or so behind the requests
    end = datetime.now(UTC)
    p50 = monitoring(
        a.service, "run.googleapis.com/request_latencies", "ALIGN_PERCENTILE_50", t_start, end, 60
    )
    p95 = monitoring(
        a.service, "run.googleapis.com/request_latencies", "ALIGN_PERCENTILE_95", t_start, end, 60
    )
    mem = monitoring(
        a.service,
        "run.googleapis.com/container/memory/utilizations",
        "ALIGN_PERCENTILE_99",
        t_start,
        end,
        60,
    )
    lat.sort()
    res = {
        "length": a.length, "n_requests": a.n, "service_reported_max_tokens": sorted(reported),
        "client_ms": {"p50": round(statistics.median(lat), 1), "p95": round(lat[int(0.95 * len(lat))], 1), "max": round(lat[-1], 1)},
        "server_ms": {"p50_max_of_minutes": round(max(p50, default=float("nan")), 1), "p95_max_of_minutes": round(max(p95, default=float("nan")), 1)},
        "memory_p99_utilization_max": round(max(mem, default=float("nan")), 3),
        "budget": {"p95_ms_lt": P95_BUDGET_MS, "memory_lt": MEMORY_BUDGET},
    }  # fmt: skip
    res["within_budget"] = bool(
        res["server_ms"]["p95_max_of_minutes"] < P95_BUDGET_MS
        and res["memory_p99_utilization_max"] < MEMORY_BUDGET
        and reported == {a.length}
    )
    a.out.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(res))


def instances(service: str) -> int:
    end = datetime.now(UTC)
    vals = []
    for state in ("active", "idle"):
        v = monitoring(
            service,
            "run.googleapis.com/container/instance_count",
            "ALIGN_MAX",
            end - timedelta(minutes=4),
            end,
            60,
            f' AND metric.labels.state="{state}"',
        )
        vals.append(int(v[-1]) if v else 0)
    return sum(vals)


def cold(a: argparse.Namespace) -> None:
    hdr = {"Authorization": "Bearer " + os.environ["CLASSIFY_ID_TOKEN"]}
    body = {
        "reviews": [
            {
                "id": "cold",
                "text": "The zip broke after a week and nobody replied to my emails, I want a refund.",
            }
        ]
    }
    samples = []
    for k in range(a.samples):
        t_wait = time.time()
        while instances(a.service) != 0:  # scale-to-zero after the idle timeout (about 15 minutes)
            time.sleep(30)
            if time.time() - t_wait > 3600:
                raise SystemExit("service never reached zero instances within an hour")
        time.sleep(30)
        with httpx.Client(timeout=120, headers=hdr) as c:
            t0 = time.perf_counter()
            r = c.post(f"{a.url.rstrip('/')}/v2/classify", json=body)
            ms = (time.perf_counter() - t0) * 1000
            warm = []
            for _ in range(10):
                t1 = time.perf_counter()
                c.post(f"{a.url.rstrip('/')}/v2/classify", json=body).raise_for_status()
                warm.append((time.perf_counter() - t1) * 1000)
        samples.append(
            {
                "cold_first_request_ms": round(ms),
                "status": r.status_code,
                "warm_p50_ms": round(statistics.median(warm), 1),
                "waited_s": round(time.time() - t_wait),
            }
        )
        print(json.dumps(samples[-1]), flush=True)
    res = {
        "service": a.service,
        "samples": samples,
        "cold_ms": sorted(s["cold_first_request_ms"] for s in samples),
    }
    a.out.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--length", type=int, required=True)
    p.add_argument("--n", type=int, default=50)
    p.add_argument("--texts", type=Path, required=True)
    p.add_argument("--tokenizer", type=Path, required=True)
    p.set_defaults(fn=probe)
    c = sub.add_parser("cold")
    c.add_argument("--samples", type=int, default=3)
    c.set_defaults(fn=cold)
    for x in (p, c):
        x.add_argument("--url", required=True)
        x.add_argument("--service", required=True)
        x.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
