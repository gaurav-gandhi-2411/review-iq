"""Nightly synthetic probe: does POST /demo/extract still work for a real,
unauthenticated caller? -- Session 12 P7b.

Context: the demo endpoint's daily quota (`DEMO_DAILY_REQUEST_BUDGET`, see
`app/api/demo.py`) exists to protect the shared Groq free-tier key real paying
customers' `/v2/extract` calls also use. A single 429 on any one probe is NOT
alarming by itself -- real visitor traffic legitimately exhausting the daily cap is
the budget working as designed, and it resets at midnight UTC. What IS alarming is
the 429 persisting past that reset -- e.g. a `DEMO_DAILY_REQUEST_BUDGET_OVERRIDE_
EXPIRES_AT` override (see PR #173) left at 0 well past its TTL, or the quota-check
DB path itself stuck failing closed (`_check_demo_quota`'s fail-closed behavior --
see app/api/demo.py -- turns a transient DB blip into a 429 for every caller until
it clears).

So this probe is deliberately debounced: a 429 on this run is recorded but does NOT
page anyone by itself. It only escalates to a real (`ci-alert`-labeled) alert once
this is the SECOND consecutive nightly run to see it -- i.e. the condition survived
a full midnight-UTC daily reset, which a legitimate one-day traffic spike cannot do.
State across runs is the repo's existing GitHub-issue-based mechanism (same one
`schedule-failure-alert` uses elsewhere), reused here with an extra label so a
first-occurrence 429 stays a quiet, non-paging tracking issue instead of "the
alert" -- see `.github/workflows/demo-quota-probe.yml` for the escalation logic
that consumes this script's exit code.

Exit codes (consumed by the calling workflow, not just human-readable):
    0 -- 200, demo endpoint genuinely worked for an unauthenticated caller.
    2 -- 429, quota exhausted -- debounce candidate, NOT an immediate hard failure.
    1 -- anything else (5xx, timeout, connection error, unexpected body) -- an
         ordinary, immediate failure; no debounce, this is unambiguously broken.

Cost: 1 real POST /demo/extract per night, on the free tier the same as any other
demo visitor. Uses a fresh (timestamp-salted) review text so the endpoint's own LRU
cache can never serve a false pass -- see app/api/demo.py's cache-hit rationale.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass

import httpx

_TIMEOUT_SECONDS = 30.0
_DEFAULT_URL = "https://api.samidhareviews.xyz/demo/extract"

EXIT_OK = 0
EXIT_HARD_FAILURE = 1
EXIT_QUOTA_EXHAUSTED = 2


@dataclass
class ProbeResult:
    exit_code: int
    status_code: int | None
    latency_ms: int
    detail: str


def probe_demo_endpoint(url: str) -> ProbeResult:
    """POST a single, never-before-seen review to `url`; classify the outcome."""
    text = f"Probe review for the P7b demo-quota tripwire, run at {time.time()!r}."
    t0 = time.monotonic()
    try:
        resp = httpx.post(url, json={"text": text}, timeout=_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 -- any transport failure is a finding, not a crash
        latency_ms = int((time.monotonic() - t0) * 1000)
        return ProbeResult(EXIT_HARD_FAILURE, None, latency_ms, f"{type(exc).__name__}: {exc}")
    latency_ms = int((time.monotonic() - t0) * 1000)

    if resp.status_code == 200:
        body = resp.json()
        if "sentiment" not in body:
            return ProbeResult(
                EXIT_HARD_FAILURE,
                200,
                latency_ms,
                f"HTTP 200 but response body missing expected 'sentiment' field: "
                f"{str(body)[:200]!r}",
            )
        return ProbeResult(EXIT_OK, 200, latency_ms, "ok")

    if resp.status_code == 429:
        return ProbeResult(
            EXIT_QUOTA_EXHAUSTED,
            429,
            latency_ms,
            "quota exhausted -- debounce candidate, not an immediate alert",
        )

    return ProbeResult(
        EXIT_HARD_FAILURE,
        resp.status_code,
        latency_ms,
        f"unexpected status {resp.status_code}: {resp.text[:200]!r}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=_DEFAULT_URL, help="demo extract endpoint to probe")
    args = parser.parse_args()

    result = probe_demo_endpoint(args.url)

    code = result.status_code if result.status_code is not None else "---"
    print(f"[demo-quota-probe] {code} {result.latency_ms}ms -- {result.detail}")

    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
