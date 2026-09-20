"""CI gate: no migration under supabase/migrations/ may GRANT the BYPASSRLS attribute to a
role, unless the (role, file) pair is in ALLOWLIST below with a reason.

Why this exists: both ephemeral-Postgres jobs (bypassrls-container-check.yml and
pre-cutover-verification.yml) apply ALL migrations and THEN apply
supabase/cutover/20260801000001_statement4_revoke_bypassrls.sql (`ALTER ROLE review_iq_app
NOBYPASSRLS`). A new migration containing `ALTER ROLE review_iq_app BYPASSRLS;` therefore
leaves both jobs green (verified with a throwaway PR, Session 15c), while in production
supabase/push.py applies migrations and would silently re-grant BYPASSRLS to the app role --
the only remaining detector was the post-hoc prod-DB workflow security-bypassrls-check.yml.
This check closes that gap at PR time, statically, with no database.

Usage:
    uv run python scripts/check_migrations_no_bypassrls_grant.py [MIGRATIONS_DIR]

Exit 0 if clean; exit 1 (naming file:line) on any un-allowlisted grant, on an unreadable /
undecodable / unterminated file, or if the directory has no .sql files (fail closed).

Scope: every *.sql under supabase/migrations/ (recursive). NOT supabase/cutover/ (that is
where the NOBYPASSRLS revoke lives) and NOT supabase/bootstrap_local_roles.sql.

Shapes COVERED (after stripping `--` line comments and nested `/* */` block comments, with
string literals and dollar-quoted DO/function bodies left intact so statements inside a
`DO $$ ... $$` block are scanned):
  * `ALTER ROLE|USER <r> ... BYPASSRLS` and `CREATE ROLE|USER <r> ... BYPASSRLS`, where
    the keyword is BYPASSRLS and not NOBYPASSRLS, for ANY role.
  * Multi-line statements (matched up to the terminating `;`), any keyword case, quoted
    ("review_iq_app") or unquoted role names, optional IF EXISTS / WITH.
  * Statements written inside dollar-quoted EXECUTE bodies ($f$ ... $f$), because those
    are scanned as ordinary code.
  * Conservative: any EXECUTE statement whose single-quoted string literal contains
    BYPASSRLS (not preceded by an identifier character, so NOBYPASSRLS is ignored). It is
    reported with the pseudo-role "<EXECUTE>" and must be allowlisted like any other hit.

Shapes NOT covered (a clean run does NOT prove these absent):
  * Dynamic SQL that assembles the keyword from pieces ('BYPASS' || 'RLS', chr(), format()
    arguments) -- only a literal BYPASSRLS token in an EXECUTE string is detected.
  * Granting the effect indirectly: SET ROLE / role membership (GRANT <bypass-role> TO x),
    ALTER DEFAULT PRIVILEGES, superuser creation, or altering pg_authid directly.
  * Files outside supabase/migrations/ (e.g. scripts/ or app code issuing ALTER ROLE).
  * The live database state -- see .github/workflows/security-bypassrls-check.yml.
  * An allowlist entry covers EVERY hit for that (role, file), not one specific line.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"

# (lower-cased role name, migration file name) -> why this grant is legitimate. Only add
# an entry with a reason a reviewer can check; anything else is a real finding.
ALLOWLIST: dict[tuple[str, str], str] = {
    ("review_iq_app", "20260726000001_review_iq_app_role.sql"): (
        "Inside `IF NOT EXISTS (... rolname = 'review_iq_app')`: granted only when the role "
        "is first created (fresh from-scratch build); never re-asserted on an existing "
        "role. The cutover file revokes it afterward in every ephemeral job."
    ),
    ("review_iq_migrator", "20260801000001_role_separation_bypassrls_remediation.sql"): (
        "Intended: review_iq_migrator is the dedicated DDL/migration role that legitimately "
        "holds BYPASSRLS (S0 role separation); it is not the runtime app role."
    ),
    ("review_iq_admin", "20260801000001_role_separation_bypassrls_remediation.sql"): (
        "Intended: review_iq_admin is the separate private-admin-service role that "
        "legitimately holds BYPASSRLS (S0 role separation); not the runtime app role."
    ),
}

EXECUTE_PSEUDO_ROLE = "<EXECUTE>"

_IDENT = r"[A-Za-z0-9_]"
_BYPASS = rf"(?<!{_IDENT})BYPASSRLS(?!{_IDENT})"
_ROLE_STMT = re.compile(
    r"(?<![A-Za-z0-9_])(?:ALTER|CREATE)\s+(?:ROLE|USER)\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?"
    r'("(?:[^"]|"")+"|[^\s;]+)[^;]*?' + _BYPASS,
    re.IGNORECASE,
)
_EXECUTE = re.compile(r"(?<![A-Za-z0-9_])EXECUTE(?![A-Za-z0-9_])", re.IGNORECASE)
_BYPASS_RE = re.compile(_BYPASS, re.IGNORECASE)


class ScanError(Exception):
    """A file could not be read or tokenized; the guard must fail closed on it."""


def _strip(sql: str) -> tuple[str, str]:
    """Return (text, code), both the same length as `sql`.

    Comments are blanked to spaces in both (newlines kept so offsets map to line numbers).
    `code` additionally blanks the CONTENT of single-quoted strings, so `;` or keywords
    inside literals cannot split or fake a statement. Raises ScanError on an unterminated
    comment or string (fail closed rather than guess where it ends).
    """
    text: list[str] = []
    code: list[str] = []
    i, n = 0, len(sql)

    def emit(chars: str) -> None:
        text.append(chars)
        code.append(chars)

    def blank(chars: str) -> str:
        return re.sub(r"[^\n]", " ", chars)

    while i < n:
        ch = sql[i]
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j == -1 else j
            text.append(blank(sql[i:j]))
            code.append(blank(sql[i:j]))
            i = j
        elif sql.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if sql.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif sql.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            if depth:
                raise ScanError("unterminated /* block comment")
            text.append(blank(sql[i:j]))
            code.append(blank(sql[i:j]))
            i = j
        elif ch == "'":
            escapes = i > 0 and sql[i - 1] in "eE" and (i < 2 or not re.match(_IDENT, sql[i - 2]))
            j = i + 1
            while True:
                if j >= n:
                    raise ScanError("unterminated string literal")
                if escapes and sql[j] == "\\":
                    j += 2
                elif sql[j] == "'":
                    if sql.startswith("''", j):
                        j += 2
                    else:
                        break
                else:
                    j += 1
            text.append(sql[i : j + 1])
            code.append("'" + blank(sql[i + 1 : j]) + "'")
            i = j + 1
        elif ch == '"':
            j = i + 1
            while True:
                if j >= n:
                    raise ScanError("unterminated quoted identifier")
                if sql[j] == '"':
                    if sql.startswith('""', j):
                        j += 2
                    else:
                        break
                else:
                    j += 1
            emit(sql[i : j + 1])
            i = j + 1
        else:
            emit(ch)
            i += 1
    return "".join(text), "".join(code)


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def scan_sql(sql: str) -> list[tuple[str, int]]:
    """Return (role, line) for every BYPASSRLS grant shape found in `sql`."""
    text, code = _strip(sql)
    hits: list[tuple[str, int]] = []
    for m in _ROLE_STMT.finditer(code):
        role = m.group(1)
        if role.startswith('"'):
            role = role[1:-1].replace('""', '"')
        hits.append((role.lower(), _line_of(code, m.start())))
    for m in _EXECUTE.finditer(code):
        end = code.find(";", m.end())
        end = len(code) if end == -1 else end
        for b in _BYPASS_RE.finditer(text, m.end(), end):
            # Only count occurrences inside a string literal: outside one, code[] still
            # holds the token and the role-statement scan above already reports it.
            if code[b.start() : b.end()] != b.group(0):
                hits.append((EXECUTE_PSEUDO_ROLE, _line_of(text, m.start())))
                break
    return hits


def check(migrations_dir: Path, allowlist: dict[tuple[str, str], str] | None = None) -> list[str]:
    """Return one failure message per un-allowlisted grant (or unreadable file)."""
    allow = ALLOWLIST if allowlist is None else allowlist
    files = sorted(migrations_dir.rglob("*.sql")) if migrations_dir.is_dir() else []
    if not files:
        return [f"{migrations_dir}: no .sql files found (refusing to pass on an empty scan)"]
    failures: list[str] = []
    for path in files:
        rel = path.relative_to(migrations_dir).as_posix()
        try:
            hits = scan_sql(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, ScanError) as exc:
            failures.append(f"{rel}: cannot scan ({type(exc).__name__}: {exc}); failing closed")
            continue
        for role, line in hits:
            if (role, path.name) in allow:
                continue
            failures.append(
                f"{rel}:{line}: grants BYPASSRLS to role {role!r} and (role, file) is not "
                f"in ALLOWLIST."
            )
    return failures


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) > 1:
        print("usage: check_migrations_no_bypassrls_grant.py [MIGRATIONS_DIR]", file=sys.stderr)
        return 2
    migrations_dir = Path(args[0]) if args else MIGRATIONS_DIR
    failures = check(migrations_dir)
    if failures:
        print("FAIL: migration(s) grant BYPASSRLS outside the allowlist:\n", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        print(
            "\nWhy: the ephemeral CI jobs revoke review_iq_app's BYPASSRLS after applying all "
            "migrations, so they cannot catch this; production's push.py would apply it. "
            "Fix: remove the grant, or add a reasoned (role, file) ALLOWLIST entry in "
            "scripts/check_migrations_no_bypassrls_grant.py.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
