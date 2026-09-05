"""CI gate: fail on placeholder `href="#"` links and a broken demo-data fixture.

Why this exists: Wave 1 spec Section C ("Domain consolidation") requires a link-health CI
job that fails on any 404/`href="#"` (spec §4.C, verification gate §5.1). Two concrete
defects motivated this: the footer GitHub link on `site/index.html` was a dead
`href="#"` placeholder (D3, fixed alongside this script), and the demo gallery previously
failed to load `site/demo-data.json` in production (D1). This is the CI-side static
regression guard for both classes of bug -- it cannot replace a live-network check, but it
catches a re-introduced placeholder link or a broken/missing demo fixture at build time
instead of in front of a customer.

What this checks:
1. Every tracked `site/*.html`, `site/docs/*.html`, `README.md`, and `docs/**/*.md` file for
   a literal bare `href="#"` (a true placeholder). Legitimate in-page anchors like
   `href="#gallery"` or `href="#live"` are NOT flagged -- only an empty fragment. In `.md`
   files, occurrences inside inline code spans (`` `href="#"` ``) or fenced code blocks are
   also not flagged -- a spec/doc describing the defect in backtick-quoted prose (e.g.
   docs/specs/wave1-commercialization.md's own D3 writeup and gate text) is not a live
   anchor tag, and treating it as one is a false positive discovered when this scanner first
   saw that file in a rebase. `.html` files get no such exemption: a real `href="#"` there is
   always a live broken link regardless of surrounding markup.
2. `site/demo-data.json` exists on disk and parses as valid UTF-8 JSON.
3. `site/index.html`'s capability gallery actually renders *something real* with
   JavaScript disabled. The gallery is entirely JS-populated (`fetch("./demo-data.json")`
   inside a `DOMContentLoaded` handler, writing into an initially-empty
   `#gallery-panels` div) with no `<noscript>` fallback -- verified directly (raw HTML
   fetched with zero JS execution, and confirmed live in a real browser with the page's
   own JS running that `<noscript>` content is inertly parsed as text, never displayed,
   so adding one carries no visual-regression risk when JS *is* enabled) that the page's
   former claim "works with JS disabled" was false: with JS off, a visitor saw a
   completely blank gallery section, not even the (also JS-gated) error-fallback message.
   This check fails if `site/index.html` has no `<noscript>` block, or if that block's
   content is too short / doesn't look like a real rendered example (no JSON-shaped
   output) to be more than a placeholder satisfying the letter of "has a noscript tag."

LIMITATION -- read before trusting this check: this is a static, offline check. It does not
follow real HTTP links or verify they return 200 (no live-network check, by design -- CI
must not depend on external hosts being reachable). It catches the exact regression classes
named above, not a general link-rot scanner. Check 3 does not execute a real browser either
-- it inspects the `<noscript>` markup textually, the same way a JS-disabled browser's
initial HTML parse would see it.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_DATA_PATH = REPO_ROOT / "site" / "demo-data.json"
GALLERY_HTML_PATH = REPO_ROOT / "site" / "index.html"

NOSCRIPT_RE = re.compile(r"<noscript>(.*?)</noscript>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")

# A real rendered example has structured output (product/sentiment/etc as a JSON-shaped
# object), not just a sentence. Cheap heuristic: an opening and closing curly brace with
# at least one `"key":` pair between them, which a placeholder sentence won't have.
_JSON_SHAPED_RE = re.compile(r'\{[^{}]*"[a-zA-Z_]+"\s*:', re.DOTALL)

# Below this many characters of stripped text, treat the noscript block as a placeholder
# rather than a genuine rendered example (e.g. just "JavaScript required." is 20 chars).
MIN_NOSCRIPT_TEXT_LENGTH = 150

# A literal bare `#` fragment -- `href="#"` or `href='#'`. Deliberately does NOT match
# `href="#gallery"` etc: the closing quote must immediately follow the `#`.
BARE_HASH_RE = re.compile(r"""href\s*=\s*(["'])#\1""")

# Inline code span: backtick-delimited text on a single line, e.g. `href="#"`.
_INLINE_CODE_SPAN_RE = re.compile(r"`[^`\n]*`")

FENCE_RE = re.compile(r"^\s*```")


def _strip_markdown_code(text: str) -> str:
    """Blank out fenced code blocks and inline code spans in Markdown source.

    A spec/doc can legitimately *describe* `href="#"` in backtick-quoted prose without
    that being a live anchor tag -- only relevant for .md files, never for .html (an
    `href="#"` inside an HTML file is always a real, live attribute).
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    in_fence = False
    for line in lines:
        if FENCE_RE.match(line):
            in_fence = not in_fence
            out.append("\n")
            continue
        if in_fence:
            out.append("\n")
            continue
        out.append(_INLINE_CODE_SPAN_RE.sub(lambda m: " " * len(m.group(0)), line))
    return "".join(out)


def _tracked_link_health_files() -> list[Path]:
    """Return tracked files this check scans, via `git ls-files`."""
    result = subprocess.run(
        ["git", "ls-files", "site/*.html", "site/docs/*.html", "README.md", "docs/**/*.md"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [REPO_ROOT / p for p in result.stdout.splitlines() if p.strip()]


def find_bare_hash_links(path: Path) -> list[tuple[int, str]]:
    """Return [(1-based line number, line text), ...] for bare `href="#"` placeholder links.

    Only flags a true empty-fragment href (`href="#"` / `href='#'`). An in-page anchor
    target such as `href="#gallery"` is a legitimate link and is never flagged. For `.md`
    files, occurrences inside inline code spans or fenced code blocks are also not flagged
    (see module docstring) -- `.html` files are scanned as-is, no exemption.
    """
    text = path.read_text(encoding="utf-8")
    scan_text = _strip_markdown_code(text) if path.suffix == ".md" else text
    original_lines = text.splitlines()
    scan_lines = scan_text.splitlines()
    violations: list[tuple[int, str]] = []
    for i, scan_line in enumerate(scan_lines, start=1):
        if BARE_HASH_RE.search(scan_line):
            violations.append((i, original_lines[i - 1].strip()))
    return violations


def _display_path(path: Path) -> str:
    """Return `path` relative to REPO_ROOT when possible, else its raw string form.

    Falls back to the raw path (rather than raising) so this also works for paths outside
    the repo root, e.g. a tmp_path fixture in tests.
    """
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def check_demo_data() -> str | None:
    """Return an error message if `site/demo-data.json` is missing or invalid, else None."""
    if not DEMO_DATA_PATH.exists():
        return f"{_display_path(DEMO_DATA_PATH)} does not exist."
    try:
        raw = DEMO_DATA_PATH.read_text(encoding="utf-8")
        json.loads(raw)
    except UnicodeDecodeError as exc:
        return f"{_display_path(DEMO_DATA_PATH)} is not valid UTF-8: {exc}"
    except json.JSONDecodeError as exc:
        return f"{_display_path(DEMO_DATA_PATH)} is not valid JSON: {exc}"
    return None


def check_noscript_gallery_fallback() -> str | None:
    """Return an error message if the gallery's no-JS fallback is missing or a placeholder.

    Simulates what a JS-disabled browser's initial HTML parse sees: extracts the raw
    `<noscript>...</noscript>` markup (never executing any script), strips tags, and
    requires enough real text with JSON-shaped output to be an actual rendered example --
    not just a `<noscript>` tag that technically exists but shows nothing meaningful.
    """
    if not GALLERY_HTML_PATH.exists():
        return f"{_display_path(GALLERY_HTML_PATH)} does not exist."

    html = GALLERY_HTML_PATH.read_text(encoding="utf-8")
    match = NOSCRIPT_RE.search(html)
    if not match:
        return (
            f"{_display_path(GALLERY_HTML_PATH)} has no <noscript> fallback -- the "
            "capability gallery is entirely JS-populated with no non-JS content path."
        )

    noscript_html = match.group(1)
    stripped_text = _TAG_RE.sub(" ", noscript_html)
    stripped_text = re.sub(r"\s+", " ", stripped_text).strip()

    if len(stripped_text) < MIN_NOSCRIPT_TEXT_LENGTH:
        return (
            f"{_display_path(GALLERY_HTML_PATH)}'s <noscript> block has only "
            f"{len(stripped_text)} characters of text ({MIN_NOSCRIPT_TEXT_LENGTH} "
            "required) -- looks like a placeholder, not a real rendered example."
        )

    if not _JSON_SHAPED_RE.search(noscript_html):
        return (
            f"{_display_path(GALLERY_HTML_PATH)}'s <noscript> block has no JSON-shaped "
            'output (a `{ "key": ... }` structure) -- the page claims examples show '
            "structured extraction output; the no-JS fallback should too."
        )

    return None


def main() -> int:
    any_violations = False

    for path in _tracked_link_health_files():
        if not path.exists():
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        violations = find_bare_hash_links(path)
        if violations:
            any_violations = True
            print(f"\n{rel}:")
            for line_no, line_text in violations:
                print(f"  line {line_no}: {line_text}")

    demo_data_error = check_demo_data()
    if demo_data_error:
        any_violations = True
        print(f"\n{demo_data_error}")

    noscript_error = check_noscript_gallery_fallback()
    if noscript_error:
        any_violations = True
        print(f"\n{noscript_error}")

    if any_violations:
        print(
            "\nFAIL: link-health check failed -- see above. Fix by either replacing the "
            'placeholder `href="#"` with a real destination, restoring/repairing '
            "site/demo-data.json, or adding/fixing the gallery's <noscript> fallback.",
            file=sys.stderr,
        )
        return 1

    print(
        'OK: no bare href="#" placeholder links found, site/demo-data.json is valid, and '
        "the gallery's no-JS fallback renders a real example."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
