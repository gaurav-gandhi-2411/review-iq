"""Run current prod extraction (PROMPT_VERSION v2.3, tiered Groq routing) against the
real-data vernacular benchmark candidates. Uses benchmark.systems.review_iq.predict —
the SAME wrapper used by the existing internal benchmark, i.e. the actual production
pipeline (sanitize -> language-aware prompt -> tiered routing), not a reimplementation.

Cassette path is redirected to this benchmark's own file so the v0.1 internal benchmark's
cassette is never touched.

This step does NOT require gold labels — it just runs and saves predictions. Scoring
against human gold happens in score_against_gold.py once GG has labeled a batch.

INCIDENT, 2026-07-07: an unpaced first run (210 back-to-back real calls) blew through
Groq's per-minute token budget (6000 TPM on the small model; ~1800 tokens/call) and
degraded PRODUCTION, because this script shared prod's real GROQ_API_KEY. A second,
paced retry (25s between outer calls) degraded prod AGAIN — pacing the outer loop
doesn't bound the burst, because a single predict() call can internally fire 2-4 real
Groq requests almost instantly (retry + escalation). The actual fix: this script now
runs on a DEDICATED benchmark-only Groq key (see benchmark_groq_key.py), loaded and
injected BEFORE any other import — so app.core.config.get_settings() (prod's cached
Settings singleton) picks up the benchmark key for this process's whole lifetime, and
prod's real key/quota is never touched no matter how bursty this run gets. Pacing is
kept as a courtesy to the benchmark key's own budget, not as a prod-protection measure.

Usage:
    uv run python benchmark/vernacular_v2/run_predictions.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Must happen before ANY import that could transitively trigger app.core.config.get_settings()
# (which is @lru_cache'd — whatever GROQ_API_KEY is in the env on its first call sticks for
# the rest of this process). This env var is scoped to THIS local script process only; it has
# no effect on prod's separately-configured Cloud Run container.
from benchmark.vernacular_v2.benchmark_groq_key import load_benchmark_groq_key  # noqa: E402

_benchmark_key = load_benchmark_groq_key()
os.environ["GROQ_API_KEY"] = _benchmark_key
print(
    f"Using dedicated benchmark Groq key ({_benchmark_key[:8]}...{_benchmark_key[-4:]}) — isolated from prod."
)

import asyncio  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402

CANDIDATES_PATH = ROOT / "benchmark" / "vernacular_v2" / "candidates.jsonl"
PREDICTIONS_PATH = ROOT / "benchmark" / "vernacular_v2" / "predictions.jsonl"

# Courtesy pacing on the benchmark key's own budget (not a prod-protection measure anymore).
# Reduced 2026-07-08: background runs keep getting killed externally before finishing at 25s/call
# (~40min for the remaining batch) — confirmed dedicated-key quota is healthy at this pace via
# direct rate-limit-header checks, so shortening this is a speed change only, not a safety one.
DELAY_SECONDS = 5


async def main() -> None:
    import app.core.providers.cassette as cassette_module
    from benchmark.systems.review_iq import predict

    # Redirect the cassette path AFTER importing the predict() wrapper: importing
    # benchmark.systems.review_iq re-assigns CASSETTES_PATH to the v0.1 internal
    # benchmark's cassette at module level, so a redirect done before that import is
    # silently clobbered (real bug found 2026-07-09 — earlier vernacular runs leaked
    # recordings into benchmark/cassettes/review_iq_cassettes.json this way).
    cassette_module.CASSETTES_PATH = ROOT / "benchmark" / "vernacular_v2" / "cassettes.json"

    candidates = [
        json.loads(line)
        for line in CANDIDATES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    # Only a genuinely SUCCESSFUL prior prediction (no _error) counts as "already done" —
    # the 2026-07-07 incident's 145 failures must be retried, not skipped.
    existing: dict[str, dict] = {}
    if PREDICTIONS_PATH.exists():
        for line in PREDICTIONS_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                if not rec.get("pred", {}).get("_error"):
                    existing[rec["id"]] = rec

    to_retry = len(candidates) - len(existing)
    eta_min = round(to_retry * DELAY_SECONDS / 60, 1)
    print(
        f"Candidates: {len(candidates)}  Already succeeded: {len(existing)}  To retry: {to_retry}"
    )
    print(f"Pacing: {DELAY_SECONDS}s/call  ETA: ~{eta_min} min")

    results = list(existing.values())
    # Rewrite the file with only successes; retries below append fresh attempts for the rest.
    with PREDICTIONS_PATH.open("w", encoding="utf-8") as fh:
        for rec in results:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()

        for i, cand in enumerate(candidates, 1):
            if cand["id"] in existing:
                continue
            t0 = time.monotonic()
            pred = await predict(cand["text"], replay_mode=False)
            latency_ms = int((time.monotonic() - t0) * 1000)
            rec = {
                "id": cand["id"],
                "slice": cand["slice"],
                "language_detected": cand["language_detected"],
                "pred": pred,
                "latency_ms": latency_ms,
            }
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            results.append(rec)
            err = pred.get("_error", "")
            status = f"SENT={pred.get('SENT')} URG={pred.get('URG')} LANG={pred.get('LANG')}"
            if err:
                status = f"ERROR: {err[:80]}"
            print(
                f"  [{i}/{len(candidates)}] {cand['id']} ({cand['slice']}): {status}  {latency_ms}ms"
            )
            await asyncio.sleep(DELAY_SECONDS)

    print(f"\nDone. Total predictions: {len(results)}")
    print(f"Written: {PREDICTIONS_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
