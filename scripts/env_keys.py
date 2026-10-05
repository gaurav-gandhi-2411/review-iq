"""List the variable NAMES a dotenv-style file sets -- never their values.

The sanctioned way to answer "does this .env define X?" without putting a secret in a transcript
(see scripts/hook_guard_secrets.py, which denies `cat .env` and friends). Prints one line per
variable: `NAME  set|empty`. Exits 1 if a file is missing so a typo is not read as "no variables".

Usage: python scripts/env_keys.py [FILE ...]   (default: .env)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def keys(text: str) -> list[tuple[str, bool]]:
    """(name, has_value) per assignment, in file order. Values are reduced to a bool here."""
    out: list[tuple[str, bool]] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = _LINE.match(line)
        if m:
            out.append((m.group(1), bool(m.group(2).strip().strip("\"'"))))
    return out


def main(argv: list[str]) -> int:
    rc = 0
    for name in argv or [".env"]:
        p = Path(name)
        if not p.is_file():
            print(f"{name}: not found", file=sys.stderr)
            rc = 1
            continue
        print(f"[{name}]")
        for k, has_value in keys(p.read_text(encoding="utf-8", errors="replace")):
            print(f"  {k}  {'set' if has_value else 'empty'}")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
