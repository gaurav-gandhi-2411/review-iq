"""CI gate: fail on any HIGH/CRITICAL npm advisory that is not on a dated allowlist (S17).

Why not plain `npm audit --audit-level=high`: GHSA-vfj7-8cjw-p6xm (`braces` <= 3.0.3, deeply nested
glob patterns -> stack exhaustion) has NO patched release in the 3.x line, and the only fix npm
offers is tailwindcss 4 (a breaking PostCSS migration, Dependabot #106, held). `braces` is reached
only through tailwindcss 3 -> chokidar/fast-glob/micromatch, all devDependencies: it runs at build
time on our own source, never in the shipped bundle (`npm audit --omit=dev` reports 0). The plain
command therefore turned main red with nothing anyone could merge to fix it. An allowlist entry is a
trust in that reasoning, so it names the advisory, says why, and EXPIRES: after the date the gate
fails again and forces a decision (migrate tailwind, or renew with fresh evidence). Any other
high/critical advisory, runtime or dev, still fails the build. Fails closed if npm's output cannot
be parsed.

Usage: python scripts/npm_audit_gate.py   (from the repo root; runs `npm audit --json` in web/)
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

# advisory id -> (expires, reason)
ALLOWLIST: dict[str, tuple[date, str]] = {
    "GHSA-vfj7-8cjw-p6xm": (
        date(2026, 12, 31),
        "braces<=3.0.3 has no patched 3.x release; reached only via tailwindcss 3 (dev, build-time "
        "only; `npm audit --omit=dev` = 0). Fix needs tailwindcss 4 (PR #106, breaking).",
    ),
}
BLOCKING = {"high", "critical"}


def advisory_ids(vuln: dict[str, Any]) -> set[str]:
    """GHSA ids of the root-cause advisories on one npm vulnerability node."""
    return {
        v["url"].rsplit("/", 1)[-1]
        for v in vuln.get("via", [])
        if isinstance(v, dict) and "url" in v
    }


def evaluate(audit: dict[str, Any], today: date) -> list[str]:
    """Problems that must fail the build (empty = pass). Pure, unit-testable."""
    if "vulnerabilities" not in audit:
        return [f"unparseable npm audit output (keys: {sorted(audit)[:5]}); failing closed"]
    problems: list[str] = []
    for name, vuln in sorted(audit["vulnerabilities"].items()):
        if vuln.get("severity") not in BLOCKING:
            continue
        ids = advisory_ids(vuln)
        for adv in sorted(ids):
            entry = ALLOWLIST.get(adv)
            if entry is None:
                problems.append(f"{name}: {adv} ({vuln['severity']}) is not allowlisted")
            elif today > entry[0]:
                problems.append(f"{name}: allowlist for {adv} expired {entry[0]}: {entry[1]}")
        # A transitive node (via = package names only) is covered iff its causes are; a node whose
        # via has neither advisories nor names is something we cannot reason about.
        if not ids and not [v for v in vuln.get("via", []) if isinstance(v, str)]:
            problems.append(f"{name}: high/critical with no advisory detail; failing closed")
    return problems


def main() -> int:
    proc = subprocess.run(
        ["npm", "audit", "--json"],
        cwd=Path(__file__).resolve().parent.parent / "web",
        capture_output=True,
        text=True,
        check=False,
        shell=sys.platform == "win32",  # npm is npm.cmd on Windows
    )
    try:
        audit = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"FAIL: npm audit produced no JSON (exit {proc.returncode}): {proc.stderr[:300]}")
        return 1
    problems = evaluate(audit, date.today())
    if problems:
        print("FAIL: npm audit gate\n  " + "\n  ".join(problems))
        return 1
    n = sum(1 for v in audit["vulnerabilities"].values() if v.get("severity") in BLOCKING)
    print(f"OK: {n} high/critical node(s), every advisory allowlisted and unexpired")
    return 0


if __name__ == "__main__":
    sys.exit(main())
