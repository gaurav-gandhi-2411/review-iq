"""Closed-loop concurrency-ramp load harness (asyncio streams), deterministic seed 42.

For each scenario and each concurrency level N it runs N workers for --step-seconds; each
worker sends its next request as soon as the previous one returns. Reports req/s, latency
p50/p95/p99 and error rate per step, plus the knee. Spawns scripts/load/mock_server.py on
127.0.0.1 itself unless --base-url is given (then it targets that URL; never point it at a
production service: the mock server is the only supported target, see docs/ops/load-test-s20.md).

Usage: python scripts/load/harness.py --out reports/load/run.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

SEED = 42
CONCURRENCY = (1, 2, 4, 8, 16, 32, 64, 128)
# Hard cap on total wall-clock so a mis-set flag cannot run away.
MAX_TOTAL_SECONDS = 1800
REQUEST_TIMEOUT_S = 30.0
SERVER = Path(__file__).with_name("mock_server.py")

_PHRASES = [
    "Great sound quality but the battery dies after 3 hours.",
    "Delivery was late and the box was damaged, would not buy again.",
    "Works fine for the price, build feels cheap though.",
    "Excellent value, comfortable fit, recommend to friends.",
    "Stopped charging after two weeks. Support never replied.",
]


@dataclass(frozen=True)
class Scenario:
    name: str
    method: str
    path: str
    llm_ms: int = 0  # which mock server config this scenario needs
    bare: bool = False  # control: no-op ASGI app, measures the harness/loopback ceiling


SCENARIOS = [
    Scenario("control_bare", "GET", "/health", bare=True),
    Scenario("health", "GET", "/health"),
    Scenario("root_404", "GET", "/"),
    Scenario("v2_reviews", "GET", "/v2/reviews?limit=50"),
    Scenario("bff_reviews", "GET", "/bff/reviews?limit=50"),
    Scenario("extract_llm0ms", "POST", "/v2/extract", llm_ms=0),
    Scenario("extract_llm800ms", "POST", "/v2/extract", llm_ms=800),
]


@dataclass
class StepResult:
    scenario: str
    concurrency: int
    requests: int
    errors: int
    rps: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    error_rate: float


def percentile(sorted_vals: list[float], q: float) -> float:
    """Nearest-rank percentile of an ascending list (0.0 for an empty list)."""
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, math.ceil(q * len(sorted_vals)) - 1))
    return sorted_vals[idx]


def find_knee(steps: list[StepResult]) -> dict[str, int | None]:
    """Three markers of where a scenario stops scaling, each a concurrency level or None.

    saturation: first level reaching >= 90% of the peak req/s (more clients add only latency).
    p95_2x:     first level whose p95 is >= 2x the p95 at the lowest level.
    errors:     first level with an error rate above 1%.
    """
    out: dict[str, int | None] = {"saturation": None, "p95_2x": None, "errors": None}
    if not steps:
        return out
    peak = max(s.rps for s in steps)
    base = steps[0].p95_ms
    for s in steps:
        if out["saturation"] is None and s.rps >= 0.9 * peak:
            out["saturation"] = s.concurrency
        if out["p95_2x"] is None and s is not steps[0] and base > 0 and s.p95_ms >= 2 * base:
            out["p95_2x"] = s.concurrency
        if out["errors"] is None and s.error_rate > 0.01:
            out["errors"] = s.concurrency
    return out


class RawConn:
    """Minimal keep-alive HTTP/1.1 client over asyncio streams.

    httpx adds ~1 ms CPU per request and, measured on this machine (control run against a
    no-op ASGI app), collapses above ~8 workers, which would make the harness, not the server,
    the bottleneck. Only what this harness needs: GET/POST with Content-Length responses.
    """

    def __init__(self, base_url: str, headers: dict[str, str]) -> None:
        u = urlparse(base_url)
        self.host = u.hostname or "127.0.0.1"
        self.tls = u.scheme == "https"
        self.port = u.port or (443 if self.tls else 80)
        self.extra = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None

    async def request(self, method: str, path: str, body: bytes = b"") -> int:
        if self.writer is None or self.reader is None:
            self.reader, self.writer = await asyncio.open_connection(
                self.host, self.port, ssl=self.tls or None
            )
        head = f"{method} {path} HTTP/1.1\r\nHost: {self.host}\r\n{self.extra}"
        if method == "POST":
            head += f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
        self.writer.write(head.encode() + b"\r\n" + body)
        raw = await asyncio.wait_for(self.reader.readuntil(b"\r\n\r\n"), REQUEST_TIMEOUT_S)
        lines = raw.decode("latin-1").split("\r\n")
        status = int(lines[0].split(" ", 2)[1])
        length = 0
        for line in lines[1:]:
            if line.lower().startswith("content-length:"):
                length = int(line.split(":", 1)[1])
        if length:
            await asyncio.wait_for(self.reader.readexactly(length), REQUEST_TIMEOUT_S)
        return status

    async def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
            self.reader = self.writer = None


async def run_step(
    base_url: str, sc: Scenario, n: int, seconds: float, headers: dict[str, str], seed: int
) -> StepResult:
    rng = random.Random(seed + n)
    bodies = [
        json.dumps({"text": f"{rng.choice(_PHRASES)} #{rng.randrange(10**9)}"}).encode()
        for _ in range(256)
    ]
    lat: list[float] = []
    errors = 0
    deadline = time.monotonic() + seconds

    async def worker(wid: int) -> None:
        nonlocal errors
        conn = RawConn(base_url, headers)
        i = wid
        while time.monotonic() < deadline:
            t0 = time.perf_counter()
            try:
                code = await conn.request(
                    sc.method, sc.path, bodies[i % len(bodies)] if sc.method == "POST" else b""
                )
                ok = code < 400 or (sc.name == "root_404" and code == 404)
            except (TimeoutError, OSError, asyncio.IncompleteReadError, ValueError):
                ok = False
                await conn.close()  # reconnect on the next iteration
            lat.append((time.perf_counter() - t0) * 1000)
            if not ok:
                errors += 1
            i += n
        await conn.close()

    t_start = time.monotonic()
    await asyncio.gather(*(worker(w) for w in range(n)))
    elapsed = time.monotonic() - t_start
    lat.sort()
    total = len(lat)
    return StepResult(
        scenario=sc.name,
        concurrency=n,
        requests=total,
        errors=errors,
        rps=round(total / elapsed, 1),
        p50_ms=round(percentile(lat, 0.50), 1),
        p95_ms=round(percentile(lat, 0.95), 1),
        p99_ms=round(percentile(lat, 0.99), 1),
        error_rate=round(errors / total, 4) if total else 1.0,
    )


def wait_ready(base_url: str, timeout_s: float = 60.0) -> None:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        try:
            if httpx.get(f"{base_url}/health", timeout=2.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError("mock server did not become ready")


async def run_group(
    base_url: str, scenarios: list[Scenario], seconds: float, headers: dict[str, str], t_end: float
) -> list[StepResult]:
    out: list[StepResult] = []
    for sc in scenarios:
        for n in CONCURRENCY:
            if time.monotonic() > t_end:
                print("hard time cap reached; stopping", file=sys.stderr)
                return out
            res = await run_step(base_url, sc, n, seconds, headers, SEED)
            print(json.dumps(asdict(res)), flush=True)
            out.append(res)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step-seconds", type=float, default=20.0)
    ap.add_argument("--only", default="", help="comma-separated scenario names")
    ap.add_argument("--out", default="load-results.json")
    ap.add_argument("--port", type=int, default=18080)
    ap.add_argument("--base-url", default="", help="target an already-running mock server")
    ap.add_argument("--header", action="append", default=[], help="'Name: value' (repeatable)")
    ap.add_argument("--max-total-seconds", type=float, default=MAX_TOTAL_SECONDS)
    args = ap.parse_args()

    headers = {h.split(":", 1)[0].strip(): h.split(":", 1)[1].strip() for h in args.header}
    wanted = {s for s in args.only.split(",") if s}
    chosen = [s for s in SCENARIOS if not wanted or s.name in wanted]
    t_end = time.monotonic() + args.max_total_seconds
    results: list[StepResult] = []

    if args.base_url:
        results = asyncio.run(run_group(args.base_url, chosen, args.step_seconds, headers, t_end))
    else:
        for bare, llm_ms in sorted({(s.bare, s.llm_ms) for s in chosen}):
            group = [s for s in chosen if (s.bare, s.llm_ms) == (bare, llm_ms)]
            base = f"http://127.0.0.1:{args.port}"
            cmd = [sys.executable, str(SERVER), "--port", str(args.port), "--llm-ms", str(llm_ms)]
            if bare:
                cmd.append("--bare")
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                wait_ready(base)
                results += asyncio.run(run_group(base, group, args.step_seconds, headers, t_end))
            finally:
                proc.terminate()  # our own child, by handle: never a name-based kill
                proc.wait(timeout=15)

    knees: dict[str, Any] = {
        name: find_knee([r for r in results if r.scenario == name])
        for name in dict.fromkeys(r.scenario for r in results)
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(
            {
                "seed": SEED,
                "step_seconds": args.step_seconds,
                "knees": knees,
                "steps": [asdict(r) for r in results],
            },
            indent=2,
        )
    )
    print("knees:", knees)


if __name__ == "__main__":
    main()
