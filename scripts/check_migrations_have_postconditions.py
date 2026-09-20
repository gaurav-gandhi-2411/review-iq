"""CI gate: every migration under supabase/migrations/ declares >= 1 well-formed postcondition
(or is in supabase/postconditions.py's ALLOWLIST with a reason). Stdlib only, fails closed.

Why this exists: a migration that "ran without error" is not a migration whose effect is true.
20260912000003 issued a REVOKE as a role that did not own the function; Postgres completed it
without error and changed nothing, and supabase/push.py would have ledgered it as applied
while production kept the grant. push.py now runs each file's `-- @postcondition:` SQL before
ledgering it -- but it can only run postconditions that EXIST. This guard makes "a new
migration with no postcondition" a PR-time failure instead of a push.py refusal at deploy time.

Usage:
    uv run python scripts/check_migrations_have_postconditions.py [MIGRATIONS_DIR]

The grammar and the single-SELECT rules live in supabase/postconditions.py (one definition,
shared with push.py) -- see its docstring.

Exit 0 if clean; exit 1 (naming file and reason) if any file:
  * has no postcondition and no ALLOWLIST entry, or an ALLOWLIST entry with an empty reason;
  * has a malformed / orphaned / duplicate block, or SQL that is not a single SELECT
    (multiple statements, comment markers, `$` quoting, INTO, locking clauses, a denylisted
    side-effecting function);
  * contains its own top-level BEGIN/COMMIT/ROLLBACK (breaks push.py's atomic apply);
  * is unreadable / not UTF-8;
  * is allowlisted although it now HAS postconditions, or an ALLOWLIST key names no file
    (stale entries would silently widen the exemption).
Also exit 1 if the directory is missing or contains no .sql file (never pass an empty scan).

Scope: every *.sql under supabase/migrations/ (recursive). NOT supabase/cutover/ (applied by
hand, outside push.py) and NOT supabase/ci/ bootstrap SQL.

What a clean run does NOT prove: that a postcondition is TRUE (that needs a database -- the
pre-cutover-verification job runs push.py, which executes them, on every migration PR) or that
it is non-vacuous (tests/integration/test_migration_postconditions.py undoes the effect of a
representative set and checks the condition goes false). This guard checks form only.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"
_SUPABASE_DIR = REPO_ROOT / "supabase"

# Shared grammar/allowlist module (stdlib only). It lives beside push.py, which is run as a
# script (so its directory is on sys.path there); the guard puts it on the path explicitly.
if str(_SUPABASE_DIR) not in sys.path:
    sys.path.insert(0, str(_SUPABASE_DIR))

import postconditions as pc  # noqa: E402  (must follow the sys.path insert above)


def check(migrations_dir: Path, allowlist: dict[str, str] | None = None) -> list[str]:
    """Return one failure message per problem found (empty list == clean)."""
    allow = pc.ALLOWLIST if allowlist is None else allowlist
    files = sorted(migrations_dir.rglob("*.sql")) if migrations_dir.is_dir() else []
    if not files:
        return [f"{migrations_dir}: no .sql files found (refusing to pass on an empty scan)"]
    failures: list[str] = []
    names = {p.name for p in files}
    for key, reason in allow.items():
        if key not in names:
            failures.append(f"ALLOWLIST entry {key!r} names no migration file (stale entry)")
        if not isinstance(reason, str) or not reason.strip():
            failures.append(f"ALLOWLIST entry {key!r} has an empty reason")
    for path in files:
        rel = path.relative_to(migrations_dir).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            failures.append(f"{rel}: cannot read ({type(exc).__name__}: {exc}); failing closed")
            continue
        control = pc.find_transaction_control(text)
        if control:
            failures.append(
                f"{rel}: contains its own transaction control ({control!r}); push.py wraps each "
                "file + its postconditions + the ledger row in one transaction"
            )
        try:
            found = pc.parse_postconditions(text)
        except pc.PostconditionError as exc:
            failures.append(f"{rel}: malformed postcondition: {exc}")
            continue
        if found:
            if path.name in allow:
                failures.append(
                    f"{rel}: has postconditions but is also in ALLOWLIST -- remove the stale "
                    "allowlist entry"
                )
            continue
        if path.name in allow:
            continue  # an empty reason was already reported in the allowlist loop above
        failures.append(
            f"{rel}: no '-- @postcondition:' block and no ALLOWLIST entry (see the grammar in "
            "supabase/postconditions.py)"
        )
    return failures


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) > 1:
        print("usage: check_migrations_have_postconditions.py [MIGRATIONS_DIR]", file=sys.stderr)
        return 2
    migrations_dir = Path(args[0]) if args else MIGRATIONS_DIR
    failures = check(migrations_dir)
    if failures:
        print("FAIL: migration postcondition check:\n", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        print(
            "\nWhy: push.py refuses to apply/ledger a migration with no postcondition, and a "
            "migration whose effect is never checked can 'succeed' while changing nothing. "
            "Fix: add `-- @postcondition: <name>` + `-- SQL: SELECT <boolean>` (see "
            "supabase/postconditions.py), or an ALLOWLIST entry with a reason there.",
            file=sys.stderr,
        )
        return 1
    print(f"OK: every migration under {migrations_dir} declares a well-formed postcondition.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
