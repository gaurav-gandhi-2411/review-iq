"""Local Docker benchmark of POST /v2/classify. NOT Cloud Run: numbers are for one container on a shared, loaded dev machine.

    PYTHONPATH=. python scripts/bench_classify.py --image review-iq-classify:a6local --cpus 1 --memory 1g \
        --texts D:/ml-cache/review-intent/scale.jsonl --host-parity <classify_parity output> --out <json>

Starts a container (named below; only that container is ever removed), measures: cold start (docker run -> first 200 from
/v2/classify, with the model-load part read from the app's own `classify.model_loaded` log line), peak memory from the
container's cgroup (memory.peak), latency of >= 200 sequential requests at batch 1 (all 757 sealed texts) and batch 8
(200 requests), and in-container decision parity (the container's per-item decisions on the sealed set vs the offline
decisions; the mismatch set must equal the host run's `served_per_item` set). Review text is sent, never stored or printed.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import statistics
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from app.core.classify import runtime

from scripts.classify_parity import PRED_DIR, offline_decisions
from scripts.classify_parity import TASKS as TASKS

NAME = "a6-classify-bench"
PORT = 18080
WARM = "Great product, arrived on time."  # benign literal used only to detect readiness


def sh(*args: str, check: bool = True, both: bool = False) -> str:
    """stdout only by default (docker prints warnings on stderr that would corrupt `inspect` values); `both` for logs."""
    r = subprocess.run(args, capture_output=True, text=True, check=check)  # noqa: S603
    return (r.stdout + r.stderr if both else r.stdout).strip()


def post(reviews: list[dict]) -> tuple[int, dict, float]:
    req = urllib.request.Request(  # noqa: S310
        f"http://127.0.0.1:{PORT}/v2/classify", data=json.dumps({"reviews": reviews}).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )  # fmt: skip
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=300) as r:  # noqa: S310
        body = json.loads(r.read())
        return r.status, body, (time.perf_counter() - t0) * 1000


def pct(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(q * len(s)))], 1)


def stats(xs: list[float]) -> dict:
    return {
        "n": len(xs),
        "p50_ms": pct(xs, 0.5),
        "p95_ms": pct(xs, 0.95),
        "mean_ms": round(statistics.mean(xs), 1),
    }


def cgroup(path: str) -> int | None:
    out = sh("docker", "exec", NAME, "cat", path, check=False)
    return int(out) if out.isdigit() else None


def proc_status_bytes(field: str) -> int | None:
    """A kB field of /proc/1/status (PID 1 = the single uvicorn worker): VmHWM = peak RSS, VmRSS = current RSS.
    cgroup memory.peak also counts page cache, so it is reported separately."""
    out = sh("docker", "exec", NAME, "cat", "/proc/1/status", check=False)
    m = re.search(rf"{field}:\s+(\d+) kB", out)
    return int(m.group(1)) * 1024 if m else None


def peak_rss_bytes() -> int | None:
    return proc_status_bytes("VmHWM")


def write(res: dict, out: str) -> None:
    res["host"] = {
        "platform": platform.platform(),
        "note": "shared dev machine under load; latency pessimistic",
    }
    res["pred_dir"] = str(PRED_DIR.relative_to(PRED_DIR.parents[3]))
    Path(out).write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "host"}, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--cpus", default="1")
    ap.add_argument("--memory", default="1g")
    ap.add_argument("--texts", required=True)
    ap.add_argument("--host-parity", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--quick", action="store_true", help="stop after the first request (memory probe)"
    )
    a = ap.parse_args()
    from engine.intent.labels import load_texts
    from engine.intent.llm_moat import sealed_ids

    th = runtime.load_thresholds()
    ids, off = offline_decisions(th)
    sealed = [i for i in ids if i in set(sealed_ids())]
    texts = load_texts(Path(a.texts))
    host = json.loads(Path(a.host_parity).read_text(encoding="utf-8"))
    host_set = {(m["id"], m["task"]) for m in host["modes"]["served_per_item"]["mismatches"]}
    bench = Path(__file__).with_name("bench_classify_app.py")
    sh("docker", "rm", "-f", NAME, check=False)  # only our own named container
    t_start = time.perf_counter()
    sh(
        "docker", "run", "-d", "--name", NAME, "--cpus", a.cpus, "--memory", a.memory, "--memory-swap", a.memory,
        "-p", f"{PORT}:8080", "-e", "ENABLE_V2_CLASSIFY=true", "-e", "PYTHONPATH=/app:/bench",
        "-v", f"{bench}:/bench/bench_classify_app.py:ro", a.image,
        "uvicorn", "bench_classify_app:app", "--host", "0.0.0.0", "--port", "8080",
    )  # fmt: skip
    res: dict = {"image": a.image, "docker_cpus": a.cpus, "docker_memory": a.memory}
    try:
        first = None
        while first is None:
            try:
                st, body, ms = post([{"text": WARM}])
                first = (st, body, ms)
            except urllib.error.HTTPError as e:  # the app is up but refused: record it, do not spin
                res["http_error_on_first_request"] = e.code
                write(res, a.out)
                return
            except Exception:  # noqa: BLE001 -- connection refused / reset while the app boots
                if sh("docker", "inspect", "-f", "{{.State.Running}}", NAME) != "true":
                    fmt = "exit={{.State.ExitCode}} oom_killed={{.State.OOMKilled}}"
                    res["died_before_first_response"] = sh("docker", "inspect", "-f", fmt, NAME)
                    res["log_tail"] = sh("docker", "logs", "--tail", "8", NAME, both=True)[-1200:]
                    write(res, a.out)
                    return
                res["idle_rss_bytes"] = proc_status_bytes("VmRSS") or res.get("idle_rss_bytes")
                time.sleep(0.25)
        res["cold_start_s"] = round(time.perf_counter() - t_start, 2)
        res["first_request_ms"] = round(first[2], 1)
        m = re.search(
            r"load_s\W+([0-9.]+)",
            re.sub(r"\x1b\[[0-9;]*m", "", sh("docker", "logs", NAME, both=True)),
        )
        res["model_load_s"] = float(m.group(1)) if m else None
        res["peak_rss_after_first_request_bytes"] = peak_rss_bytes()
        res["oom_killed"] = sh("docker", "inspect", "-f", "{{.State.OOMKilled}}", NAME)
        if a.quick:
            write(res, a.out)
            return
        # batch 1: every sealed text once (also the in-container parity pass)
        lat1, got = [], {}
        for i in sealed:
            _, body, ms = post([{"id": i, "text": texts[i]}])
            lat1.append(ms)
            got[i] = body["results"][0]["tasks"]
        res["batch_1"] = stats(lat1)
        k = {i: n for n, i in enumerate(ids)}
        mism = {
            (i, t) for i in sealed for t in TASKS if got[i][t]["label"] != off[t]["labels"][k[i]]
        }
        res["in_container_parity"] = {
            "n_items": len(sealed), "n_mismatches_vs_offline": len(mism),
            "mismatch_set_equals_host_served_per_item": mism == host_set,
            "only_in_container": len(mism - host_set),
            "only_in_host": len(host_set - mism),
        }  # fmt: skip
        # batch 8: 200 requests of 8 consecutive sealed texts (wrapping)
        lat8 = []
        for r in range(200):
            batch = [{"text": texts[sealed[(r * 8 + j) % len(sealed)]]} for j in range(8)]
            lat8.append(post(batch)[2])
        res["batch_8"] = stats(lat8)
        res["peak_rss_bytes"] = peak_rss_bytes()
        res["cgroup_peak_incl_page_cache_bytes"] = cgroup("/sys/fs/cgroup/memory.peak")
        res["memory_limit_bytes"] = cgroup("/sys/fs/cgroup/memory.max")
        res["oom_killed"] = sh("docker", "inspect", "-f", "{{.State.OOMKilled}}", NAME)
    finally:
        sh("docker", "rm", "-f", NAME, check=False)
    write(res, a.out)


if __name__ == "__main__":
    main()
