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

Cost: 4 GET requests/night against domains this project already owns, +1 authenticated
GET if PROBE_API_KEY is set. $0.

Authenticated path (added 2026-08-01, BYPASSRLS remediation P2): none of the four
surfaces above exercise an authenticated request. That gap matters because an RLS
default-deny on the self-serve key-management endpoints returns empty/404 rather
than an error -- a silent-wrong-answer failure that looks identical to a healthy 200
to a probe that only checks reachability, and this probe as it stood would not have
caught it had it ever reached production. (No such outage actually occurred this
pass -- the regression this remediation responds to was a DB-level issue caught via
direct query and rolled back before serving any traffic; Cloud Logging confirms zero
5xx/ERROR entries throughout. This probe extension is a preventive gap-close, not a
response to a confirmed incident.) If PROBE_API_KEY is set (a real, live,
low-quota `riq_live_...` key for a dedicated synthetic probe org -- provisioning is a
manual step, see the comment above _API_BASE_URL below), this probe now also GETs
/v2/reviews with that key and fails if the response isn't a well-formed 200 with a
"results" list -- proving the api_key -> org resolution -> RLS-scoped read path
actually works end-to-end, not just that a public page loads. Skipped gracefully
(logged, not a failure) if the secret isn't configured, same pattern as
--slack-webhook above.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from dataclasses import dataclass

import httpx

_TIMEOUT_SECONDS = 15.0
_BRAND_MARKER = "Samidha Reviews"
_API_BASE_URL = "https://api.samidhareviews.xyz"

# Provisioning PROBE_API_KEY (manual, one-time -- not performed by this script or by
# CI): create a dedicated org via the normal signup path (or app/api/admin.py's
# operator CRUD) named something like "synthetic-probe", quota=10 (the probe issues
# one request/night -- headroom for manual re-runs, never meant to serve real
# traffic), and store the resulting riq_live_* key as the PROBE_API_KEY GitHub
# Actions secret. Rotate it the same way any other credential in
# ops/runbooks/secret-rotation.md is rotated.


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


async def probe_authenticated_path(client: httpx.AsyncClient, api_key: str) -> ProbeResult:
    """GET /v2/reviews with a real api_key -- exercises resolve_org_for_api_key_prefix
    -> _set_tenant() -> the actual RLS-scoped read, not just page reachability. Fails
    on non-200 OR a response that isn't well-formed JSON with a "results" list --
    catches exactly the failure class this probe was missing: a 200 with an empty or
    malformed body from an RLS default-deny, which a bare status-code check would
    silently treat as healthy."""
    name, url = "authenticated-v2-reviews", f"{_API_BASE_URL}/v2/reviews"
    t0 = time.monotonic()
    try:
        resp = await client.get(
            url,
            headers={"X-API-Key": api_key},
            timeout=_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
    except Exception as exc:  # noqa: BLE001 -- any failure here is a probe finding, not a crash
        latency_ms = int((time.monotonic() - t0) * 1000)
        return ProbeResult(name, url, False, None, latency_ms, f"{type(exc).__name__}: {exc}")
    latency_ms = int((time.monotonic() - t0) * 1000)

    if resp.status_code != 200:
        return ProbeResult(
            name,
            url,
            False,
            resp.status_code,
            latency_ms,
            f"expected HTTP 200, got {resp.status_code} -- body: {resp.text[:200]!r}",
        )
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        return ProbeResult(
            name,
            url,
            False,
            resp.status_code,
            latency_ms,
            f"HTTP 200 but body is not valid JSON: {resp.text[:200]!r}",
        )
    if "results" not in body or not isinstance(body["results"], list):
        return ProbeResult(
            name,
            url,
            False,
            resp.status_code,
            latency_ms,
            f'HTTP 200 but no "results" list in body (keys: {list(body.keys())}) -- '
            "this is the exact silent-failure shape an RLS default-deny produces",
        )
    return ProbeResult(name, url, True, resp.status_code, latency_ms, "ok")


async def run_probe() -> list[ProbeResult]:
    """Probe every surface concurrently and return their results.

    Includes the authenticated path if PROBE_API_KEY is set (see module docstring);
    skipped, not failed, if it isn't -- same optional-check pattern as --slack-webhook.
    """
    async with httpx.AsyncClient() as client:
        results = list(await asyncio.gather(*(probe_surface(client, s) for s in _SURFACES)))
        api_key = os.environ.get("PROBE_API_KEY", "")
        if api_key:
            results.append(await probe_authenticated_path(client, api_key))
        else:
            print("  (PROBE_API_KEY not set -- skipping authenticated-path probe)")
        return results


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
