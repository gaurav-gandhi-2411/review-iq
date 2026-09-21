"""Browser check for site/index.html: responsive widths, above-the-fold demo, rendered contrast.

Why this exists: `scripts/check_contrast.py` only validates the palette pairs documented in
design/tokens.json -- it says so itself ("cannot catch a page that uses an undocumented
colour combination"). Session 15c C8 redesigned the marketing page, so this script closes
that gap by loading the real page in Chromium and measuring what is actually rendered:

1. Loads site/index.html over a local HTTP server (demo-data.json needs http, not file://)
   at 360, 390, 768 and 1280 px wide and screenshots each.
2. Asserts no horizontal scroll (documentElement.scrollWidth <= innerWidth) at every width.
3. Asserts the hero demo textarea is fully inside the first screen at 1280x800 and 390x844,
   and that a visitor can type into it there without scrolling.
4. Walks every visible text node, resolves its effective foreground/background (compositing
   alpha down the ancestor chain), and checks WCAG 2.x contrast: 4.5:1 body, 3:1 large text
   (>= 24px, or >= 18.66px bold).
5. Exercises the demo widget and lead form state machines against mocked network responses
   (200 / 429 / 422 / abort), so the loading/error/success states are executed, not assumed.

Requires `playwright` with a Chromium build. Playwright is NOT declared in pyproject.toml, so
this is a developer/verification tool, not a CI gate; run it with any interpreter that has
playwright installed (do not install into a global environment -- use a throwaway venv):

    python scripts/check_site_responsive.py --label after --out reports/screenshots/s15c-c8

`--label before --no-assert` only captures screenshots (used for the pre-redesign baseline,
which predates the ids/states this script asserts on).

LIMITATION: contrast is measured for default state text only -- not hover/focus/disabled
variants, and not text drawn inside images or SVG. Those pairs are checked by hand in the PR.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, Route, sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
SITE_DIR = REPO_ROOT / "site"

# (width, height): the two above-the-fold checks use the sizes the brief names; the others
# only need a plausible phone/tablet height because they assert horizontal overflow only.
VIEWPORTS: list[tuple[int, int]] = [(360, 780), (390, 844), (768, 1024), (1280, 800)]
FOLD_CHECKED: set[int] = {390, 1280}

# Runs in the page: returns one record per visible text-bearing element with its resolved
# colours. Colours are normalised through a 1x1 canvas because getComputedStyle can return
# color(srgb ...) for color-mix() values, which is not directly parseable as rgb().
COLLECT_TEXT_JS = """
() => {
  const cv = document.createElement('canvas'); cv.width = cv.height = 1;
  const cx = cv.getContext('2d', { willReadFrequently: true });
  const parse = (css) => {
    // Resolve alpha by rendering the colour over black and over white.
    cx.clearRect(0, 0, 1, 1); cx.fillStyle = '#000'; cx.fillRect(0, 0, 1, 1);
    cx.fillStyle = css; cx.fillRect(0, 0, 1, 1);
    const b = cx.getImageData(0, 0, 1, 1).data;
    cx.clearRect(0, 0, 1, 1); cx.fillStyle = '#fff'; cx.fillRect(0, 0, 1, 1);
    cx.fillStyle = css; cx.fillRect(0, 0, 1, 1);
    const w = cx.getImageData(0, 0, 1, 1).data;
    // over black: c*a ; over white: c*a + 255*(1-a)  => a = 1 - (w-b)/255
    const a = 1 - (w[0] - b[0]) / 255;
    if (a <= 0.001) return [0, 0, 0, 0];
    return [b[0] / a, b[1] / a, b[2] / a, a];
  };
  const over = (top, bottom) => {
    const a = top[3] + bottom[3] * (1 - top[3]);
    if (a === 0) return [0, 0, 0, 0];
    return [0, 1, 2].map(i => (top[i] * top[3] + bottom[i] * bottom[3] * (1 - top[3])) / a).concat([a]);
  };
  const effectiveBg = (el) => {
    const chain = [];
    for (let n = el; n; n = n.parentElement) chain.push(n);
    let acc = [255, 255, 255, 1];
    for (let i = chain.length - 1; i >= 0; i--) {
      const bg = parse(getComputedStyle(chain[i]).backgroundColor);
      if (bg[3] > 0) acc = over(bg, acc);
    }
    return acc;
  };
  const out = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  for (let t = walker.nextNode(); t; t = walker.nextNode()) {
    if (!t.textContent.trim()) continue;
    const el = t.parentElement;
    if (!el || seen.has(el)) continue;
    seen.add(el);
    if (el.closest('[aria-hidden="true"]') || el.closest('script,style,noscript')) continue;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    if (r.width === 0 || r.height === 0 || cs.visibility === 'hidden' || cs.display === 'none') continue;
    if (el.closest('[hidden]')) continue;
    const bg = effectiveBg(el);
    const fgRaw = parse(cs.color);
    const fg = over(fgRaw, bg);
    out.push({
      text: t.textContent.trim().slice(0, 40),
      tag: el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.split(' ')[0] : ''),
      fg: fg.slice(0, 3).map(Math.round), bg: bg.slice(0, 3).map(Math.round),
      size: parseFloat(cs.fontSize), weight: parseInt(cs.fontWeight, 10) || 400,
    });
  }
  return out;
}
"""


def _lin(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb: list[int]) -> float:
    """WCAG relative luminance of an [r, g, b] triple (0-255 ints)."""
    r, g, b = (_lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: list[int], b: list[int]) -> float:
    """WCAG contrast ratio between two [r, g, b] triples (always >= 1.0)."""
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def required_ratio(size_px: float, weight: int) -> float:
    """WCAG large-text rule: >= 24px, or >= 18.66px and bold, needs 3:1; else 4.5:1."""
    return 3.0 if size_px >= 24 or (size_px >= 18.66 and weight >= 700) else 4.5


def serve(page_path: str = "index.html") -> tuple[http.server.ThreadingHTTPServer, str]:
    """Serve site/ on an ephemeral localhost port; returns (server, url of `page_path`)."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE_DIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/{page_path}"


def check_widths(
    page: Page, url: str, out_dir: Path, label: str, check_fold: bool = True
) -> list[str]:
    """Screenshot every viewport and return failure messages for overflow / fold checks.

    `check_fold` is index.html-specific (the hero demo textarea); pass False for other pages.
    """
    failures: list[str] = []
    for width, height in VIEWPORTS:
        page.set_viewport_size({"width": width, "height": height})
        page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(300)
        metrics = page.evaluate(
            "() => ({sw: document.documentElement.scrollWidth, iw: window.innerWidth})"
        )
        overflow_ok = metrics["sw"] <= metrics["iw"]
        print(
            f"[{'PASS' if overflow_ok else 'FAIL'}] {width}px no horizontal scroll: "
            f"scrollWidth={metrics['sw']} innerWidth={metrics['iw']}"
        )
        if not overflow_ok:
            failures.append(f"{width}px: horizontal scroll ({metrics['sw']} > {metrics['iw']})")
        page.screenshot(path=str(out_dir / f"{label}-{width}-fold.png"))
        page.screenshot(path=str(out_dir / f"{label}-{width}-full.png"), full_page=True)
        if check_fold and width in FOLD_CHECKED:
            box = page.locator("#live-text").bounding_box()
            in_fold = bool(box) and box["y"] >= 0 and box["y"] + box["height"] <= height
            print(
                f"[{'PASS' if in_fold else 'FAIL'}] {width}x{height} #live-text inside the "
                f"first screen: box={box}"
            )
            if not in_fold:
                failures.append(f"{width}x{height}: textarea not fully above the fold ({box})")
            else:
                page.locator("#live-text").click()
                page.keyboard.type("Superb earphone yaar, battery bahut weak hai.")
                typed = page.locator("#live-text").input_value()
                scrolled = page.evaluate("() => window.scrollY")
                typed_ok = typed.startswith("Superb earphone") and scrolled == 0
                print(
                    f"[{'PASS' if typed_ok else 'FAIL'}] {width}x{height} typed into textarea "
                    f"without scrolling: scrollY={scrolled}"
                )
                if not typed_ok:
                    failures.append(f"{width}x{height}: typing failed or page scrolled")
    return failures


def check_contrast(page: Page, url: str, ready_selector: str | None = None) -> list[str]:
    """Measure every visible text node's contrast at desktop + mobile; return failures.

    `ready_selector` waits for JS-rendered content first (index.html's demo gallery).
    """
    failures: list[str] = []
    worst: dict[str, float] = {}
    for width, height in ((1280, 800), (390, 844)):
        page.set_viewport_size({"width": width, "height": height})
        page.goto(url, wait_until="networkidle")
        if ready_selector:
            page.wait_for_selector(ready_selector, state="attached", timeout=5000)
        records: list[dict[str, Any]] = page.evaluate(COLLECT_TEXT_JS)
        pairs: dict[tuple[Any, ...], float] = {}
        for rec in records:
            ratio = contrast(rec["fg"], rec["bg"])
            need = required_ratio(rec["size"], rec["weight"])
            key = (tuple(rec["fg"]), tuple(rec["bg"]), need)
            pairs[key] = min(ratio, pairs.get(key, 99.0))
            if ratio < need:
                failures.append(
                    f"{width}px {rec['tag']} {rec['text']!r}: {ratio:.2f} < {need} "
                    f"(fg={rec['fg']} bg={rec['bg']} {rec['size']}px/{rec['weight']})"
                )
        ok = not any(f.startswith(f"{width}px") for f in failures)
        print(
            f"[{'PASS' if ok else 'FAIL'}] {width}px contrast: {len(records)} text elements, "
            f"{len(pairs)} distinct fg/bg/size-class pairs"
        )
        for (fg, bg, need), ratio in sorted(pairs.items(), key=lambda kv: kv[1])[:6]:
            print(f"        lowest: {ratio:.2f} (need {need}) fg={list(fg)} bg={list(bg)}")
        worst[str(width)] = min(pairs.values())
    print(f"minimum measured ratio per width: {json.dumps(worst)}")
    return failures


def _fulfil_json(route: Route, status: int, body: dict[str, Any]) -> None:
    route.fulfill(
        status=status,
        content_type="application/json",
        headers={"access-control-allow-origin": "*"},
        body=json.dumps(body),
    )


def _json_handler(status: int, body: dict[str, Any]) -> Callable[[Route], None]:
    """Route handler answering every request with a fixed JSON status/body (closure, not a
    default-arg lambda: Playwright passes (route, request) to two-arg callables)."""

    def handler(route: Route) -> None:
        _fulfil_json(route, status, body)

    return handler


def _text_handler(status: int, body: str) -> Callable[[Route], None]:
    """Route handler answering with a non-JSON text body (e.g. a 404 or a proxy HTML page)."""

    def handler(route: Route) -> None:
        route.fulfill(
            status=status,
            content_type="text/plain",
            headers={"access-control-allow-origin": "*"},
            body=body,
        )

    return handler


def check_states(page: Page, url: str) -> list[str]:
    """Drive the demo widget and lead form through their states with mocked responses."""
    failures: list[str] = []

    def expect(name: str, cond: bool) -> None:
        print(f"[{'PASS' if cond else 'FAIL'}] {name}")
        if not cond:
            failures.append(name)

    page.set_viewport_size({"width": 1280, "height": 800})

    # --- demo widget -----------------------------------------------------------------
    calls: list[dict[str, Any]] = []

    def demo_ok(route: Route) -> None:
        if route.request.method == "OPTIONS":
            route.fulfill(
                status=204,
                headers={"access-control-allow-origin": "*", "access-control-allow-headers": "*"},
            )
            return
        calls.append(json.loads(route.request.post_data or "{}"))
        _fulfil_json(route, 200, {"sentiment": "mixed", "urgency": "low"})

    page.route("**/demo/extract", demo_ok)
    page.goto(url, wait_until="networkidle")
    page.locator("#live-btn").click()
    expect(
        "demo: empty submit shows inline hint and makes no request",
        page.locator("#live-empty").is_visible() and not calls,
    )
    page.locator("#live-text").fill("Battery bahut weak hai.")
    page.locator("#live-btn").click()
    page.wait_for_selector("#live-result-wrap:not([hidden])", timeout=3000)
    expect(
        "demo: success renders highlighted JSON and posts {text}",
        calls == [{"text": "Battery bahut weak hai."}]
        and "sentiment" in page.locator("#live-result").inner_text(),
    )
    page.unroute("**/demo/extract")

    page.route("**/demo/extract", lambda r: _fulfil_json(r, 429, {"detail": "rate"}))
    page.locator("#live-btn").click()
    page.wait_for_selector("#live-error:not([hidden])", timeout=3000)
    expect(
        "demo: 429 shows the friendly rate-limit copy",
        "5 requests/min" in page.locator("#live-error").inner_text(),
    )
    page.unroute("**/demo/extract")

    page.route("**/demo/extract", lambda r: r.abort())
    page.locator("#live-btn").click()
    page.wait_for_selector("#live-error:not([hidden])", timeout=3000)
    expect(
        "demo: network failure shows the same friendly copy, button re-enabled",
        page.locator("#live-btn").is_enabled(),
    )
    page.unroute("**/demo/extract")

    # --- lead form -------------------------------------------------------------------
    payloads: list[dict[str, Any]] = []

    def leads_ok(route: Route) -> None:
        if route.request.method == "OPTIONS":
            route.fulfill(
                status=204,
                headers={"access-control-allow-origin": "*", "access-control-allow-headers": "*"},
            )
            return
        payloads.append(json.loads(route.request.post_data or "{}"))
        _fulfil_json(route, 202, {"ok": True})

    page.route("**/leads", leads_ok)
    page.goto(url, wait_until="networkidle")
    page.locator("#lead-submit").click()
    expect(
        "lead: empty submit shows inline errors and posts nothing",
        page.locator("#lead-name-error").is_visible() and not payloads,
    )
    page.locator("#lead-name").fill("Asha Rao")
    page.locator("#lead-email").fill("not-an-email")
    page.locator("#lead-submit").click()
    expect(
        "lead: malformed email is rejected inline",
        page.locator("#lead-email-error").is_visible() and not payloads,
    )
    page.locator("#lead-email").fill("asha@example.com")
    page.locator("#lead-company").fill("Acme D2C")
    page.locator("#lead-brands").fill("12")
    page.locator("#lead-volume").select_option(index=2)
    page.locator("#lead-submit").click()
    page.wait_for_selector("#lead-success:not([hidden])", timeout=3000)
    expect("lead: success replaces the form, no reload", page.locator("#lead-form").is_hidden())
    keys = sorted(payloads[0]) if payloads else []
    expect(
        "lead: payload has exactly the contract keys",
        keys
        == sorted(
            ["name", "email", "company", "brands", "reviews_per_month", "message", "website"]
        ),
    )
    expect(
        "lead: honeypot `website` is sent empty for a human",
        bool(payloads) and payloads[0]["website"] == "",
    )
    page.unroute("**/leads")

    # 503 = lead neither stored nor emailed: must also show the hello@ fallback. Every failure
    # keeps the form and what the visitor typed.
    for status, label, wants_fallback in (
        (422, "422 validation", False),
        (429, "429 rate limit", False),
        (503, "503 temporarily_unavailable", True),
    ):
        page.route(
            "**/leads",
            _json_handler(status, {"ok": False, "error": "x", "message": f"Server said {status}."}),
        )
        page.goto(url, wait_until="networkidle")
        page.locator("#lead-name").fill("Asha Rao")
        page.locator("#lead-email").fill("asha@example.com")
        page.locator("#lead-company").fill("Acme D2C")
        page.locator("#lead-brands").fill("12")
        page.locator("#lead-volume").select_option(index=2)
        page.locator("#lead-message").fill("Twelve storefronts.")
        page.locator("#lead-submit").click()
        page.wait_for_selector("#lead-status:not([hidden])", timeout=3000)
        text = page.locator("#lead-status").inner_text()
        expect(
            f"lead: {label} shows the server's message, form and input stay",
            f"Server said {status}." in text
            and page.locator("#lead-form").is_visible()
            and page.locator("#lead-message").input_value() == "Twelve storefronts.",
        )
        expect(
            f"lead: {label} {'includes' if wants_fallback else 'need not include'} the "
            "hello@ fallback",
            ("hello@samidhareviews.xyz" in text) or not wants_fallback,
        )
        page.unroute("**/leads")

    # Endpoint not deployed yet (404, no JSON body) and a 200 that is not {"ok": true}
    # must both fail closed with actionable text, never a success state.
    for status, body_text in ((404, "not found"), (200, "<html>proxy page</html>")):
        page.route("**/leads", _text_handler(status, body_text))
        page.goto(url, wait_until="networkidle")
        page.locator("#lead-name").fill("Asha Rao")
        page.locator("#lead-email").fill("asha@example.com")
        page.locator("#lead-company").fill("Acme D2C")
        page.locator("#lead-brands").fill("12")
        page.locator("#lead-volume").select_option(index=2)
        page.locator("#lead-submit").click()
        page.wait_for_selector("#lead-status:not([hidden])", timeout=3000)
        expect(
            f"lead: unexpected {status} fails closed with the hello@ fallback, no success",
            "hello@samidhareviews.xyz" in page.locator("#lead-status").inner_text()
            and page.locator("#lead-success").is_hidden(),
        )
        page.unroute("**/leads")

    page.route("**/leads", lambda r: r.abort())
    page.goto(url, wait_until="networkidle")
    page.locator("#lead-name").fill("Asha Rao")
    page.locator("#lead-email").fill("asha@example.com")
    page.locator("#lead-company").fill("Acme D2C")
    page.locator("#lead-brands").fill("12")
    page.locator("#lead-volume").select_option(index=2)
    page.locator("#lead-submit").click()
    page.wait_for_selector("#lead-status:not([hidden])", timeout=3000)
    expect(
        "lead: network failure shows the hello@ fallback and re-enables submit",
        "hello@samidhareviews.xyz" in page.locator("#lead-status").inner_text()
        and page.locator("#lead-submit").is_enabled(),
    )
    page.unroute("**/leads")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--label", default="after", help="screenshot filename prefix")
    parser.add_argument("--out", default="reports/screenshots/s15c-c8", help="output directory")
    parser.add_argument(
        "--no-assert",
        action="store_true",
        help="screenshots only (baseline capture of the pre-redesign page)",
    )
    parser.add_argument(
        "--page",
        default="index.html",
        help="page under site/ to load (default index.html; e.g. docs/index.html). Non-index "
        "pages get width, overflow and contrast checks only.",
    )
    args = parser.parse_args()

    out_dir = (REPO_ROOT / args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    is_index = args.page == "index.html"
    server, url = serve(args.page)
    failures: list[str] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            if args.no_assert:
                for width, height in VIEWPORTS:
                    page.set_viewport_size({"width": width, "height": height})
                    page.goto(url, wait_until="networkidle")
                    page.wait_for_timeout(500)
                    page.screenshot(path=str(out_dir / f"{args.label}-{width}-fold.png"))
                    page.screenshot(
                        path=str(out_dir / f"{args.label}-{width}-full.png"), full_page=True
                    )
                    print(f"captured {args.label} at {width}px")
            else:
                failures += check_widths(page, url, out_dir, args.label, check_fold=is_index)
                failures += check_contrast(
                    page, url, "#gallery-panels .cat-panel" if is_index else None
                )
                if is_index:
                    failures += check_states(page, url)
            browser.close()
    finally:
        server.shutdown()

    if failures:
        print(f"\nFAIL: {len(failures)} check(s) failed:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("\nOK" if not args.no_assert else "\nscreenshots captured")
    return 0


if __name__ == "__main__":
    sys.exit(main())
