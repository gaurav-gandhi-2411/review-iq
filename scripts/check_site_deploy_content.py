"""Verify the just-deployed site/ content actually reached review-iq-demo.pages.dev.

Hotfix session (2026-09-13): the marketing surface went stale for two months because
CI reported green while deploying the wrong directory (web/dist, not site/) to the
wrong Cloudflare Pages project (samidha-reviews-web, which has no custom domain and
never received the required Vite secrets) -- a green check that carried no relationship
to what a visitor actually saw. A path-filtered trigger and a successful `wrangler
pages deploy` exit code are necessary but not sufficient: this script is the one check
that actually looks at the served HTML and fails the workflow if the deploy shipped the
wrong content, an empty build, or nothing new at all.

Fails closed: a fetch error, non-200, or missing marker is a hard FAIL, never a skip.
Retries a few times before failing -- Cloudflare Pages' `Cache-Control: max-age=0,
must-revalidate` means it should reflect a new deploy immediately, but this tolerates a
few seconds of edge propagation lag rather than flaking on the first check.

Usage:
    uv run --with httpx python scripts/check_site_deploy_content.py
"""

from __future__ import annotations

import sys
import time

import httpx

SITE_URL = "https://review-iq-demo.pages.dev/"
REQUIRED_STRINGS = [
    "#F6C042",  # saffron -- design/tokens.json, PR #180
    "#E8823A",  # ember -- design/tokens.json, PR #180
    'id="problem"',  # PR #181 sales-page restructure
    'id="pricing"',  # PR #181 sales-page restructure
    'id="how-it-works"',  # PR #181 sales-page restructure
]
MAX_ATTEMPTS = 5
RETRY_DELAY_SECONDS = 5


def _fail(message: str) -> int:
    print(f"FAIL: {message}")
    return 1


def fetch_site_html() -> str | None:
    """Return the live page body, or None if it couldn't be fetched cleanly."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = httpx.get(SITE_URL, timeout=15.0, follow_redirects=True)
        except httpx.HTTPError as exc:
            print(f"attempt {attempt}/{MAX_ATTEMPTS}: fetch error: {exc}")
        else:
            if resp.status_code == 200 and resp.text:
                return resp.text
            print(f"attempt {attempt}/{MAX_ATTEMPTS}: HTTP {resp.status_code}")
        if attempt < MAX_ATTEMPTS:
            time.sleep(RETRY_DELAY_SECONDS)
    return None


def main() -> int:
    html = fetch_site_html()
    if html is None:
        return _fail(f"could not fetch {SITE_URL} after {MAX_ATTEMPTS} attempts")

    missing = [marker for marker in REQUIRED_STRINGS if marker not in html]
    if missing:
        return _fail(
            f"{SITE_URL} is missing expected markers {missing} -- the deploy did not "
            "ship the current site/index.html. Do not treat the wrangler exit code "
            "alone as evidence this deploy worked."
        )

    print(f"OK: {SITE_URL} contains all {len(REQUIRED_STRINGS)} expected markers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
