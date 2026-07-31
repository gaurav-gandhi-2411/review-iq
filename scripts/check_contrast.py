"""CI gate: WCAG AA contrast verification for design/tokens.json colour pairs.

Why this exists: Wave 1 spec Section D ("Logo and visual identity") requires the brand
palette to be "WCAG AA verified with a contrast test in CI" (spec Section 4.D, verification
gate Section 5.10). `design/tokens.json` is the single source of truth for colour pairs --
both the ones that MUST pass AA (`contrastPairs`) and the ones that are documented as
intentionally-forbidden combinations (`knownFailingPairs`, e.g. white text on an ember
button). This script is the CI-side enforcement of both halves of that contract:

1. Every entry in `contrastPairs` must meet its own `minRatio` -- catches a future colour
   edit silently breaking accessibility.
2. Every entry in `knownFailingPairs` must actually still fail (within a small tolerance of
   its documented `ratio`) -- catches the opposite drift: someone "fixing" the failing
   pair's hex values (making the documentation stale) or, conversely, tightening a
   contrastPairs entry's hex so much that a mistake silently reclassifies a pair without
   anyone updating which list it lives in.

What this checks: WCAG 2.x relative-luminance contrast ratio for each pair, computed from
the raw sRGB hex values in `design/tokens.json` -- no dependency on any browser/DOM contrast
API, so it runs identically in CI and locally.

LIMITATION -- read before trusting this check: this validates the *documented* colour pairs
in tokens.json, not every pixel actually rendered on every page. It cannot catch a page that
uses an undocumented colour combination outside `usageRules`. Treat it as "the palette itself
is AA-compliant where it says it is," not "every rendered surface is automatically AA."
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TypedDict

REPO_ROOT = Path(__file__).resolve().parent.parent
TOKENS_PATH = REPO_ROOT / "design" / "tokens.json"

# Tolerance for comparing a `knownFailingPairs` entry's documented `ratio` against the
# freshly computed one -- guards against float rounding while still catching real drift.
KNOWN_FAILING_TOLERANCE = 0.02


class ContrastPair(TypedDict):
    name: str
    bg: str
    fg: str
    minRatio: float


class KnownFailingPair(TypedDict):
    name: str
    bg: str
    fg: str
    ratio: float
    rule: str


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Parse a `#RRGGBB` string into an (r, g, b) tuple of 0-255 ints."""
    value = hex_color.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"Expected a 6-digit hex colour, got {hex_color!r}")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _linearize_channel(channel_8bit: int) -> float:
    """Linearize one 0-255 sRGB channel per the WCAG relative-luminance formula."""
    c = channel_8bit / 255.0
    if c <= 0.03928:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    """Return WCAG relative luminance (0.0-1.0) for a `#RRGGBB` colour."""
    r, g, b = _hex_to_rgb(hex_color)
    r_lin, g_lin, b_lin = _linearize_channel(r), _linearize_channel(g), _linearize_channel(b)
    return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    """Return the WCAG contrast ratio between two `#RRGGBB` colours (always >= 1.0)."""
    l_a = relative_luminance(hex_a)
    l_b = relative_luminance(hex_b)
    lighter, darker = max(l_a, l_b), min(l_a, l_b)
    return (lighter + 0.05) / (darker + 0.05)


def load_tokens(path: Path) -> dict[str, object]:
    """Load and parse the design tokens JSON file at `path`."""
    return json.loads(path.read_text(encoding="utf-8"))


def check_contrast_pairs(pairs: list[ContrastPair]) -> list[str]:
    """Return a list of failure messages for any pair whose ratio is below its minRatio."""
    failures: list[str] = []
    for pair in pairs:
        ratio = contrast_ratio(pair["bg"], pair["fg"])
        if ratio < pair["minRatio"]:
            failures.append(
                f"'{pair['name']}' (bg={pair['bg']} fg={pair['fg']}): "
                f"ratio {ratio:.2f} < required minRatio {pair['minRatio']:.2f}"
            )
    return failures


def check_known_failing_pairs(pairs: list[KnownFailingPair]) -> list[str]:
    """Return a list of failure messages for any documented-failing pair that no longer
    fails as documented (either it now passes, or its measured ratio drifted from the
    recorded one beyond tolerance).
    """
    failures: list[str] = []
    for pair in pairs:
        ratio = contrast_ratio(pair["bg"], pair["fg"])
        documented_ratio = pair["ratio"]
        if abs(ratio - documented_ratio) > KNOWN_FAILING_TOLERANCE:
            failures.append(
                f"'{pair['name']}' (bg={pair['bg']} fg={pair['fg']}): computed ratio "
                f"{ratio:.2f} does not match documented ratio {documented_ratio:.2f} "
                f"(tolerance {KNOWN_FAILING_TOLERANCE}) -- update tokens.json's "
                "knownFailingPairs entry to match reality."
            )
        if ratio >= 4.5:
            failures.append(
                f"'{pair['name']}' (bg={pair['bg']} fg={pair['fg']}): documented as a "
                f"known-failing pair but its computed ratio {ratio:.2f} now passes AA "
                "(>= 4.5) -- either this pair is now safe to use (move it out of "
                "knownFailingPairs and into contrastPairs) or the hex values regressed "
                "back to failing and the documented ratio is stale."
            )
    return failures


def main() -> int:
    tokens = load_tokens(TOKENS_PATH)
    contrast_pairs: list[ContrastPair] = tokens["contrastPairs"]  # type: ignore[assignment]
    known_failing_pairs: list[KnownFailingPair] = tokens["knownFailingPairs"]  # type: ignore[assignment]

    any_failures = False

    print("Checking required AA contrast pairs (design/tokens.json -> contrastPairs):")
    for cpair in contrast_pairs:
        ratio = contrast_ratio(cpair["bg"], cpair["fg"])
        status = "PASS" if ratio >= cpair["minRatio"] else "FAIL"
        print(
            f"  [{status}] {cpair['name']}: bg={cpair['bg']} fg={cpair['fg']} "
            f"ratio={ratio:.2f} (min {cpair['minRatio']:.2f})"
        )
    pass_failures = check_contrast_pairs(contrast_pairs)
    if pass_failures:
        any_failures = True

    print("\nChecking documented known-failing pairs stay failing (knownFailingPairs):")
    for kpair in known_failing_pairs:
        ratio = contrast_ratio(kpair["bg"], kpair["fg"])
        print(
            f"  [DOCUMENTED-FAIL] {kpair['name']}: bg={kpair['bg']} fg={kpair['fg']} "
            f"ratio={ratio:.2f} (documented {kpair['ratio']:.2f})"
        )
    known_failures = check_known_failing_pairs(known_failing_pairs)
    if known_failures:
        any_failures = True

    if any_failures:
        print("\nFAIL: contrast check violations found:", file=sys.stderr)
        for message in pass_failures + known_failures:
            print(f"  - {message}", file=sys.stderr)
        return 1

    print(
        f"\nOK: all {len(contrast_pairs)} required contrast pairs meet AA, and all "
        f"{len(known_failing_pairs)} documented known-failing pairs still fail as recorded."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
