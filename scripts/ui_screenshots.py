"""Screenshot the web app's no-login surfaces at 360 and 1280 px, light and dark colour scheme (rule 15c evidence).

    python scripts/ui_screenshots.py --base http://127.0.0.1:5173 --out shots/

Pages: the login page (/), the try page (/try) and the dev-only dashboard preview in four fixture states (no backend, no login;
web/src/dev/DashboardPreview.tsx). Run against `vite dev` (the preview route exists only in DEV). Writes PNGs and a manifest.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

PAGES = {
    "login": "/",
    "try": "/try",
    "dashboard-normal": "/__preview/dashboard?state=normal",
    "dashboard-urgent": "/__preview/dashboard?state=urgent-heavy",
    "dashboard-empty": "/__preview/dashboard?state=empty",
    "dashboard-error": "/__preview/dashboard?state=error",
}
WIDTHS = (360, 1280)
SCHEMES = ("light", "dark")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    shots = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for scheme in SCHEMES:
            for w in WIDTHS:
                ctx = browser.new_context(
                    viewport={"width": w, "height": 900}, color_scheme=scheme, device_scale_factor=1
                )
                page = ctx.new_page()
                for name, path in PAGES.items():
                    page.goto(a.base.rstrip("/") + path, wait_until="networkidle")
                    page.wait_for_timeout(500)
                    f = a.out / f"{name}_{w}_{scheme}.png"
                    page.screenshot(path=str(f), full_page=True)
                    shots.append(
                        {"page": name, "path": path, "width": w, "scheme": scheme, "file": f.name}
                    )
                ctx.close()
        browser.close()
    (a.out / "manifest.json").write_text(json.dumps(shots, indent=1) + "\n", encoding="utf-8")
    print(f"{len(shots)} screenshots in {a.out}")


if __name__ == "__main__":
    main()
