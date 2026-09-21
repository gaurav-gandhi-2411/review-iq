"""Migration postcondition grammar, parser and allowlist (stdlib only).

Shared by supabase/push.py (which EXECUTES postconditions) and
scripts/check_migrations_have_postconditions.py (the static CI guard), so both read one
definition of "a well-formed postcondition" and one allowlist. Kept free of psycopg2 / dotenv
so the guard can run in the plain lint job.

Why this exists (Session 15c/15d): 20260912000003 issued `REVOKE EXECUTE ON FUNCTION
public.current_org_id() FROM PUBLIC, anon` as review_iq_migrator, which does not own that
function. A non-owner GRANT/REVOKE completes WITHOUT ERROR and changes NOTHING, so push.py
would have recorded the migration as applied while production kept the grant -- a control
that reports success without verifying the thing it claims. A migration now has to state,
as machine-readable SQL, what must be true once it has run, and push.py refuses to ledger a
file whose statement did not come true.

GRAMMAR (every migration file under supabase/migrations/ must contain >= 1 block, or be
listed in ALLOWLIST below with a reason). A block is a run of consecutive full-line `--`
comments starting at column 0:

    -- @postcondition: <snake_case_name>          name: ^[a-z][a-z0-9_]*$, unique per file
    -- @scope: prod-only | ci-only                OPTIONAL; if present it must be the very
    -- @scope-reason: <free text>                 next line, and the reason line must follow
    -- SQL: <one or more words of a SELECT>       it. Use ONLY when production and the CI
    -- SQL: <continuation of the same SELECT>     build legitimately differ.

  * One or more `-- SQL:` lines follow immediately (no blank line, no other comment between);
    their text is joined with single spaces into ONE statement.
  * That statement must be a single SELECT that returns exactly one row with one boolean
    column. TRUE passes. FALSE, NULL, zero/many rows, a non-boolean, or a SQL error all FAIL
    (fail closed -- a NULL from `x = NULL`-style logic is a failure, not a pass).
  * Static rules (checked identically by the guard and by push.py): must start with SELECT;
    at most one trailing `;` and no other `;` outside a string literal; no `--`, `/*` or `$`
    outside a string literal or quoted identifier; no INTO / FOR UPDATE|SHARE; no calls to a
    small denylist of side-effecting functions. This is defense in depth, not a sandbox: the
    real fence is that `push.py --verify` opens a READ ONLY session, and that in apply mode
    every postcondition runs inside a SAVEPOINT that is always rolled back.
  * At least one postcondition per file must be UNSCOPED (checked in every environment):
    scope is an exception mechanism, and a file whose only conditions are scoped would verify
    nothing in one of the two environments.
  * Any line starting `-- @postcondition`, `-- @scope`, `-- @scope-reason` or `-- SQL:` that
    is not part of a well-formed block is an ERROR (an orphaned or misspelled block must not
    silently degrade to "no postcondition").
  * A postcondition is evaluated at TWO moments -- right after its own file runs (in the same
    transaction, before the ledger row), and later by `--verify` against the FINAL state of
    the database. It must therefore hold at both: assert what this migration established and
    no later migration undoes (existence, columns it added and nothing renamed, the final
    grant state), not an intermediate state a later file changes.
  * Migration files must not contain their own top-level BEGIN/COMMIT/ROLLBACK: push.py runs
    each file plus its postconditions plus the ledger insert as ONE transaction, and a COMMIT
    inside the file would end it early and make the rollback-on-failure guarantee false.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Files that legitimately carry no postcondition, each with the reason. Empty after the
# 2026-09-20 backfill: every file states a checkable effect. Add an entry ONLY for a
# pure-comment or data-only migration, with a reason a reviewer can check.
ALLOWLIST: dict[str, str] = {}

VALID_SCOPES = ("prod-only", "ci-only")

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_HEADER_RE = re.compile(r"^--[ \t]*@postcondition:[ \t]*(.*?)[ \t]*$")
_SCOPE_RE = re.compile(r"^--[ \t]*@scope:[ \t]*(.*?)[ \t]*$")
_REASON_RE = re.compile(r"^--[ \t]*@scope-reason:[ \t]*(.*?)[ \t]*$")
_SQL_RE = re.compile(r"^--[ \t]*SQL:[ \t]*(.*?)[ \t]*$")
# Anything that looks like grammar at column 0 -- used to catch malformed/orphaned lines.
_ANY_GRAMMAR_RE = re.compile(r"^--[ \t]*(@[A-Za-z]|SQL[ \t]*:)", re.IGNORECASE)

_DENIED_FUNCTIONS = (
    "set_config",
    "nextval",
    "setval",
    "pg_terminate_backend",
    "pg_cancel_backend",
    "pg_reload_conf",
    "pg_sleep",
    "pg_advisory_lock",
    "pg_advisory_xact_lock",
    "dblink",
    "lo_import",
    "lo_export",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
)


class PostconditionError(Exception):
    """A postcondition block (or the file carrying it) is malformed; callers must fail closed."""


@dataclass(frozen=True)
class Postcondition:
    """One parsed postcondition. `line` is the 1-based line of its `@postcondition` header."""

    name: str
    sql: str
    line: int
    scope: str | None = None
    scope_reason: str | None = None


def applies_to(pc: Postcondition, target: str) -> bool:
    """True if `pc` should be evaluated against `target` ('prod' or 'ci')."""
    if target not in ("prod", "ci"):
        raise ValueError(f"target must be 'prod' or 'ci', got {target!r}")
    if pc.scope is None:
        return True
    return (pc.scope == "prod-only") == (target == "prod")


def _scan_sql(sql: str) -> str:
    """Return `sql` with string-literal / quoted-identifier CONTENT blanked to spaces.

    Raises PostconditionError on an unterminated quote. Only the single-quote and
    double-quote forms are understood (`''` / `""` escapes; backslash escapes only after an
    E prefix) -- dollar quoting is rejected by the caller, so it never has to be parsed.
    """
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"'):
            escapes = (
                ch == "'" and i > 0 and sql[i - 1] in "eE" and (i < 2 or not sql[i - 2].isalnum())
            )
            j = i + 1
            while True:
                if j >= n:
                    raise PostconditionError("unterminated quote in postcondition SQL")
                if escapes and sql[j] == "\\":
                    j += 2
                    continue
                if sql[j] == ch:
                    if sql.startswith(ch * 2, j):
                        j += 2
                        continue
                    break
                j += 1
            out.append(ch + " " * (j - i - 1) + ch)
            i = j + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def validate_select(sql: str) -> str:
    """Check that `sql` is a single, side-effect-free-looking SELECT; return it without a
    trailing `;`. Raises PostconditionError naming the violated rule."""
    text = sql.strip()
    if not text:
        raise PostconditionError("empty SQL")
    code = _scan_sql(text)
    if code.endswith(";"):
        text, code = text[:-1].rstrip(), code[:-1].rstrip()
    if not text:
        raise PostconditionError("empty SQL")
    if ";" in code:
        raise PostconditionError("more than one statement (a ';' outside a string literal)")
    if "--" in code or "/*" in code:
        raise PostconditionError("SQL comment marker inside the statement")
    if "$" in code:
        raise PostconditionError("'$' outside a string literal (dollar quoting is not allowed)")
    if not re.match(r"^SELECT\b", code, re.IGNORECASE):
        raise PostconditionError("must be a single SELECT (statement does not start with SELECT)")
    if re.search(r"\bINTO\b", code, re.IGNORECASE):
        raise PostconditionError("INTO is not allowed (SELECT ... INTO creates a table)")
    if re.search(r"\bFOR\s+(UPDATE|SHARE|NO\s+KEY|KEY\s+SHARE)\b", code, re.IGNORECASE):
        raise PostconditionError("row-locking clause (FOR UPDATE/SHARE) is not allowed")
    for fn in _DENIED_FUNCTIONS:
        if re.search(rf"\b{fn}\s*\(", code, re.IGNORECASE):
            raise PostconditionError(f"call to side-effecting function {fn}() is not allowed")
    return text


def parse_postconditions(sql_text: str) -> list[Postcondition]:
    """Parse every postcondition block in a migration file's text.

    Raises PostconditionError on any malformed, orphaned or duplicate block. Returns [] only
    when the file carries no grammar lines at all.
    """
    lines = sql_text.splitlines()
    found: list[Postcondition] = []
    seen: set[str] = set()
    consumed: set[int] = set()
    i = 0
    while i < len(lines):
        header = _HEADER_RE.match(lines[i])
        if not header:
            i += 1
            continue
        start = i
        lineno = i + 1
        name = header.group(1)
        if not _NAME_RE.match(name):
            raise PostconditionError(
                f"line {lineno}: postcondition name {name!r} is not snake_case ({_NAME_RE.pattern})"
            )
        if name in seen:
            raise PostconditionError(f"line {lineno}: duplicate postcondition name {name!r}")
        seen.add(name)
        i += 1
        scope: str | None = None
        reason: str | None = None
        if i < len(lines) and _SCOPE_RE.match(lines[i]):
            scope = _SCOPE_RE.match(lines[i]).group(1)  # type: ignore[union-attr]
            if scope not in VALID_SCOPES:
                raise PostconditionError(
                    f"line {i + 1}: @scope must be one of {VALID_SCOPES}, got {scope!r}"
                )
            i += 1
            rm = _REASON_RE.match(lines[i]) if i < len(lines) else None
            if not rm or not rm.group(1):
                raise PostconditionError(
                    f"line {i + 1}: @scope: {scope} must be followed immediately by a "
                    "non-empty '-- @scope-reason: <why prod and CI differ>' line"
                )
            reason = rm.group(1)
            i += 1
        parts: list[str] = []
        while i < len(lines):
            sm = _SQL_RE.match(lines[i])
            if not sm:
                break
            parts.append(sm.group(1))
            i += 1
        if not parts or not any(parts):
            raise PostconditionError(
                f"line {lineno}: postcondition {name!r} has no '-- SQL:' line immediately "
                "after its header"
            )
        try:
            sql = validate_select(" ".join(p for p in parts if p))
        except PostconditionError as exc:
            raise PostconditionError(f"line {lineno}: postcondition {name!r}: {exc}") from exc
        consumed.update(range(start, i))
        found.append(Postcondition(name, sql, lineno, scope, reason))
    for idx, line in enumerate(lines):
        if idx not in consumed and _ANY_GRAMMAR_RE.match(line):
            raise PostconditionError(
                f"line {idx + 1}: grammar line outside a well-formed postcondition block: "
                f"{line.strip()[:80]!r}"
            )
    if found and all(p.scope for p in found):
        raise PostconditionError(
            "every postcondition in the file is scoped (prod-only/ci-only); at least one "
            "unscoped postcondition is required so the file is verified in both environments"
        )
    return found


_TXN_CONTROL_RE = re.compile(
    r"^[ \t]*(BEGIN|COMMIT|ROLLBACK|ABORT|START[ \t]+TRANSACTION)([ \t]+(TRANSACTION|WORK))?[ \t]*;",
    re.IGNORECASE | re.MULTILINE,
)


def find_transaction_control(sql_text: str) -> str | None:
    """Return the first top-level BEGIN;/COMMIT;/ROLLBACK; statement found (line-start,
    outside `--` comments), or None. plpgsql's `BEGIN` inside a DO body has no semicolon
    and `END;` is deliberately not matched, so DO blocks are not false positives."""
    code = "\n".join(line.split("--", 1)[0] for line in sql_text.splitlines())
    m = _TXN_CONTROL_RE.search(code)
    return m.group(0).strip() if m else None
