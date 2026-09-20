"""Verify app.samidhareviews.xyz's React app actually mounts, not just returns 200.

Session 14 P1c: web/ is a client-side-rendered SPA -- its HTML shell
(`<div id="root"></div>`) is byte-identical whether React renders into it or crashes
before first paint. A plain HTTP fetch (curl, httpx) cannot tell the two apart; this is
exactly how the Cloudflare copy of this app shipped green while rendering a permanently
blank page (missing VITE_* env vars threw at module load, in a browser, which curl never
executes). This script launches a real headless browser so it sees what a customer's
browser sees.

Fails closed: any Playwright error, timeout, empty #root, or missing mount marker is a
hard FAIL, never a skip.

Usage:
    playwright install --with-deps chromium
    uv run --with playwright python scripts/check_app_mounted.py
"""

from __future__ import annotations

import sys

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

APP_URL = "https://app.samidhareviews.xyz/"
MOUNT_MARKER_TEXT = "Samidha Reviews"  # web/src/pages/Login.tsx's <h1>, root path is public
TIMEOUT_MS = 15_000


def _fail(message: str) -> int:
    print(f"FAIL: {message}")
    return 1


def main() -> int:
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(APP_URL, wait_until="networkidle", timeout=TIMEOUT_MS)
            root_html = page.eval_on_selector("#root", "el => el.innerHTML")
            heading_count = page.locator("h1", has_text=MOUNT_MARKER_TEXT).count()
            browser.close()
    except (PlaywrightError, PlaywrightTimeoutError) as exc:
        return _fail(f"could not verify {APP_URL} mounted: {exc}")

    if not root_html or not root_html.strip():
        return _fail(
            f"{APP_URL} returned 200 but #root is empty after networkidle -- React "
            "never mounted (this is exactly the missing-env-var failure mode found on "
            "the Cloudflare copy of this app)."
        )
    if heading_count == 0:
        return _fail(
            f"{APP_URL} rendered something into #root, but the expected "
            f"'{MOUNT_MARKER_TEXT}' heading is not present -- mounted into an "
            "unexpected state, investigate before trusting this deploy."
        )

    print(f"OK: {APP_URL} mounted React and rendered '{MOUNT_MARKER_TEXT}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
