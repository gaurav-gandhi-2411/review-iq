"""Nightly synthetic probe for customer-facing web surfaces -- Wave 2 close-out P2.

Context: `app.samidhareviews.xyz` (the dashboard) went dark -- its Vercel deployment
disappeared -- and nothing alerted. The Section F failover probe (`probe_failover.py`)
already covers the LLM call paths; this is the same discipline applied to the web
surfaces sitting in front of the product, which had no synthetic check at all before
this. Same class of bug as the dead-Gemini-model rot Section F found: config/infra
that silently decays until a human happens to look.

Each surface is checked for HTTP 200 AND a content assertion -- reachability alone is
not enough, since a parked domain, a generic host-not-found page, or a misconfigured
redirect can all return 200. The content assertion is a fixed string that would not
appear on any of those failure modes (this repo's own product name), not a check that
the page is fully functional -- proving that requires a browser, this is a cheap,
frequent, $0 tripwire.

Usage:
    uv run python scripts/probe_web_surfaces.py
    uv run python scripts/probe_web_surfaces.py --slack-webhook "$SLACK_WEBHOOK_URL"

Cost: 4 GET requests/night against domains this project already owns. $0.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from dataclasses import dataclass

import httpx

_TIMEOUT_SECONDS = 15.0
_BRAND_MARKER = "Samidha Reviews"


@dataclass
class Surface:
    """One web surface to probe."""

    name: str
    url: str
    content_marker: str = _BRAND_MARKER


@dataclass
class ProbeResult:
    """Outcome of probing one surface."""

    name: str
    url: str
    ok: bool
    status_code: int | None
    latency_ms: int
    detail: str


# Wave 2 close-out P2's exact four surfaces. /try is the same Vite SPA as the
# dashboard (client-side routed -- web/public/_redirects rewrites every path to
# index.html on Cloudflare Pages, matching what web/vercel.json did before the
# Vercel-exit migration), so a broken deployment fails it identically to the root.
_SURFACES: list[Surface] = [
    Surface("marketing", "https://samidhareviews.xyz/"),
    Surface("dashboard", "https://app.samidhareviews.xyz/"),
    Surface("api", "https://api.samidhareviews.xyz/health", content_marker='"status":"ok"'),
    Surface("try-page", "https://app.samidhareviews.xyz/try"),
]


async def probe_surface(client: httpx.AsyncClient, surface: Surface) -> ProbeResult:
    """GET one surface; fail on non-200 OR a missing content marker."""
    t0 = time.monotonic()
    try:
        resp = await client.get(surface.url, timeout=_TIMEOUT_SECONDS, follow_redirects=True)
    except Exception as exc:  # noqa: BLE001 -- any failure here is a probe finding, not a crash
        latency_ms = int((time.monotonic() - t0) * 1000)
        return ProbeResult(
            surface.name, surface.url, False, None, latency_ms, f"{type(exc).__name__}: {exc}"
        )
    latency_ms = int((time.monotonic() - t0) * 1000)

    if resp.status_code != 200:
        return ProbeResult(
            surface.name,
            surface.url,
            False,
            resp.status_code,
            latency_ms,
            f"expected HTTP 200, got {resp.status_code}",
        )

    # HTTP 200 alone is not proof of a working page -- a parked domain, a generic
    # "host not found" page, or a stale cached error all return 200 too.
    body = resp.text
    if surface.content_marker not in body:
        return ProbeResult(
            surface.name,
            surface.url,
            False,
            resp.status_code,
            latency_ms,
            f"HTTP 200 but content marker {surface.content_marker!r} not found "
            f"(first 200 chars: {body[:200]!r})",
        )

    return ProbeResult(surface.name, surface.url, True, resp.status_code, latency_ms, "ok")


async def run_probe() -> list[ProbeResult]:
    """Probe every surface concurrently and return their results."""
    async with httpx.AsyncClient() as client:
        return list(await asyncio.gather(*(probe_surface(client, s) for s in _SURFACES)))


def _notify_slack(webhook_url: str, results: list[ProbeResult]) -> None:
    """Post a failure summary to Slack, reusing eval.slack_notify's send mechanism."""
    from eval.slack_notify import post

    failed = [r for r in results if not r.ok]
    lines = [f":red_circle: *{r.name}* ({r.url}): {r.detail}" for r in failed]
    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": ":red_circle: Web surface probe FAILED",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n".join(lines)},
            },
        ]
    }
    post(webhook_url, payload)


def main() -> int:
    """Run the nightly web-surface probe. Exit 0 if all surfaces pass, 1 otherwise."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--slack-webhook", default="", help="Slack incoming webhook URL for failure alerts"
    )
    args = parser.parse_args()

    results = asyncio.run(run_probe())

    print("=== Web surface probe (Wave 2 close-out P2) ===")
    for r in results:
        status = "OK" if r.ok else "FAIL"
        code = r.status_code if r.status_code is not None else "---"
        print(f"  [{status}] {r.name:<10} {code:>3}  {r.latency_ms:>6}ms  {r.detail}")

    failed = [r for r in results if not r.ok]
    if failed:
        print(f"\n{len(failed)} of {len(results)} surface(s) FAILED:")
        for r in failed:
            print(f"  - {r.name} ({r.url}): {r.detail}")
        if args.slack_webhook:
            try:
                _notify_slack(args.slack_webhook, results)
            except Exception as exc:  # noqa: BLE001 -- alerting failure must not mask the probe failure
                print(f"  (Slack notification also failed: {exc})")
        return 1

    print("\nAll web surfaces OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
