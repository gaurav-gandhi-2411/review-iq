"""CI gate: no workflow may upload a database dump (or anything dump-shaped) unencrypted (S17).

This repo is PUBLIC and Actions artifacts are downloadable by any signed-in GitHub account, so an
artifact is a publication. Plaintext production dumps were uploaded until 2026-07-10 (four files,
deleted 2026-10-05). The db-backup workflow now enforces encryption at runtime; this gate makes the
rule survive edits to the workflow:

  * db-backup.yml's upload-artifact `path` must be exactly `upload/*.gpg` (a literal glob, not an
    expression a step output can redirect), and the job must contain the "Assert the upload set is
    encrypted-only" step before it;
  * in every workflow, an upload-artifact `path` that looks like a dump or backup (.sql, dump,
    backup, .sqlite, .db, .csv of production data) must end in `.gpg` or `.gpg*`.

Fails closed: a workflow that cannot be parsed fails the gate. Limit: it reads static `path` values;
a path assembled at runtime inside a `run:` step is covered only for db-backup.yml, by the runtime
assertion step it requires.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
DUMPISH = re.compile(r"(?i)(\.sql|dump|backup|\.sqlite|\.db\b|pg_)")
ENCRYPTED = re.compile(r"\.gpg\*?$")
GUARD_STEP = "Assert the upload set is encrypted-only"


def _steps(doc: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for job in (doc.get("jobs") or {}).values():
        out.extend(job.get("steps") or [])
    return out


def check_workflow(name: str, doc: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    steps = _steps(doc)
    for i, step in enumerate(steps):
        if "actions/upload-artifact" not in str(step.get("uses", "")):
            continue
        path = str((step.get("with") or {}).get("path", ""))
        for line in path.splitlines():
            line = line.strip()
            if line and DUMPISH.search(line) and not ENCRYPTED.search(line):
                problems.append(
                    f"{name}: upload-artifact path {line!r} looks like a dump and is not .gpg"
                )
        if name == "db-backup.yml":
            if path.strip() != "upload/*.gpg":
                problems.append(f"{name}: upload path must be exactly 'upload/*.gpg', got {path!r}")
            if not any(GUARD_STEP in str(s.get("name", "")) for s in steps[:i]):
                problems.append(f"{name}: missing the '{GUARD_STEP}' step before the upload")
    return problems


def main() -> int:
    problems: list[str] = []
    seen_backup = False
    for p in sorted(WORKFLOWS.glob("*.yml")):
        try:
            doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            problems.append(f"{p.name}: unparseable ({exc.__class__.__name__}); failing closed")
            continue
        seen_backup = seen_backup or p.name == "db-backup.yml"
        problems.extend(check_workflow(p.name, doc or {}))
    if not seen_backup:
        problems.append("db-backup.yml not found; failing closed")
    if problems:
        print("FAIL: artifact upload gate\n  " + "\n  ".join(problems))
        return 1
    print("OK: no workflow uploads a dump-shaped artifact unencrypted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
