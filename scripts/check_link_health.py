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
   `href="#gallery"` or `href="#live"` are NOT flagged -- only an empty fragment.
2. `site/demo-data.json` exists on disk and parses as valid UTF-8 JSON.

LIMITATION -- read before trusting this check: this is a static, offline check. It does not
follow real HTTP links or verify they return 200 (no live-network check, by design -- CI
must not depend on external hosts being reachable). It catches the exact regression classes
named above, not a general link-rot scanner.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_DATA_PATH = REPO_ROOT / "site" / "demo-data.json"

# A literal bare `#` fragment -- `href="#"` or `href='#'`. Deliberately does NOT match
# `href="#gallery"` etc: the closing quote must immediately follow the `#`.
BARE_HASH_RE = re.compile(r"""href\s*=\s*(["'])#\1""")


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
    target such as `href="#gallery"` is a legitimate link and is never flagged.
    """
    text = path.read_text(encoding="utf-8")
    violations: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        if BARE_HASH_RE.search(line):
            violations.append((i, line.strip()))
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

    if any_violations:
        print(
            "\nFAIL: link-health check failed -- see above. Fix by either replacing the "
            'placeholder `href="#"` with a real destination, or restoring/repairing '
            "site/demo-data.json.",
            file=sys.stderr,
        )
        return 1

    print('OK: no bare href="#" placeholder links found, and site/demo-data.json is valid.')
    return 0


if __name__ == "__main__":
    sys.exit(main())
