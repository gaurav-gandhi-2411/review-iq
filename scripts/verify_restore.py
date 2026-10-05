"""Verify that a restored database actually matches the pg_dump it was restored from (S17 H2).

A backup nobody has restored is not a backup. `psql` finishing without a crash proves little: a
restore can drop rows (COPY aborted), lose RLS (a role was missing), or restore an empty schema.
This reads the dump itself as the oracle and compares it with the restored database:

  * tables:    every `CREATE TABLE public.X` in the dump exists in the target, and no extra ones;
  * rows:      for every table the number of data lines in its `COPY public.X ... FROM stdin` block
               equals `SELECT count(*)` in the target (a table with no COPY block must be empty);
  * RLS:       tables with relrowsecurity in the target == `ENABLE ROW LEVEL SECURITY` in the dump;
  * policies:  pg_policies rows == `CREATE POLICY` statements in the dump;
  * sanity:    `--require-nonempty organizations` fails if the dump holds no organizations at all
               (an all-empty dump from a paused or wrongly scoped project looks structurally fine).

Subcommands:
    roles  DUMP.sql.gz            print the non-postgres role names the dump references, one per
                                  line (create them in the throwaway cluster before restoring, or
                                  every GRANT/POLICY that names them fails)
    verify DUMP.sql.gz DSN        compare as above; exit 1 on any mismatch

COPY text format escapes newlines inside values, so one data line == one row.
"""

from __future__ import annotations

import argparse
import gzip
import re
import sys
from collections import Counter
from pathlib import Path

_IDENT = r'"?([A-Za-z_][A-Za-z0-9_]*)"?'
_CREATE_TABLE = re.compile(rf"^CREATE TABLE (?:IF NOT EXISTS )?public\.{_IDENT}\s*\(")
_COPY = re.compile(rf"^COPY public\.{_IDENT}\s*\(.*\) FROM stdin;")
_RLS = re.compile(r"^ALTER TABLE (?:ONLY )?public\.\S+ ENABLE ROW LEVEL SECURITY;")
_POLICY = re.compile(r"^CREATE POLICY ")
# Roles appear after OWNER TO / GRANT ... TO / REVOKE ... FROM / CREATE POLICY ... TO.
_ROLE_REFS = (
    re.compile(r"OWNER TO ([A-Za-z_][A-Za-z0-9_]*);"),
    re.compile(r"^(?:GRANT|REVOKE) .+? (?:TO|FROM) ([A-Za-z_][A-Za-z0-9_, ]*);"),
    re.compile(r"^CREATE POLICY .+? TO ([A-Za-z_][A-Za-z0-9_, ]*)(?: USING| WITH CHECK|;)"),
)
_BUILTIN_ROLES = {"postgres", "public", "current_user", "session_user"}


def _lines(path: Path) -> list[str]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:  # type: ignore[operator]
        return f.read().splitlines()


def parse_dump(lines: list[str]) -> dict[str, object]:
    """Tables, per-table COPY row counts, RLS/policy statement counts from the dump text."""
    tables: set[str] = set()
    rows: Counter[str] = Counter()
    rls = policies = 0
    in_copy: str | None = None
    for line in lines:
        if in_copy is not None:
            if line == r"\.":
                in_copy = None
            else:
                rows[in_copy] += 1
            continue
        if m := _CREATE_TABLE.match(line):
            tables.add(m.group(1))
        elif m := _COPY.match(line):
            in_copy = m.group(1)
            rows.setdefault(in_copy, 0)
        elif _RLS.match(line):
            rls += 1
        elif _POLICY.match(line):
            policies += 1
    return {"tables": tables, "rows": rows, "rls": rls, "policies": policies}


def roles_referenced(lines: list[str]) -> list[str]:
    found: set[str] = set()
    in_copy = False
    for line in lines:
        if in_copy:
            in_copy = line != r"\."
            continue
        if line.startswith("COPY "):
            in_copy = True
            continue
        for pat in _ROLE_REFS:
            if m := pat.search(line):
                found.update(r.strip() for r in m.group(1).split(","))
    return sorted(r for r in found if r.lower() not in _BUILTIN_ROLES)


def compare(
    expected: dict[str, object],
    actual_rows: dict[str, int],
    actual_rls: int,
    actual_policies: int,
    require_nonempty: str | None,
) -> list[str]:
    problems: list[str] = []
    exp_tables: set[str] = expected["tables"]  # type: ignore[assignment]
    exp_rows: Counter[str] = expected["rows"]  # type: ignore[assignment]
    if not exp_tables:
        problems.append("dump contains no CREATE TABLE public.* (empty or wrong-schema dump)")
    for t in sorted(exp_tables - set(actual_rows)):
        problems.append(f"table public.{t} is in the dump but missing after restore")
    for t in sorted(set(actual_rows) - exp_tables):
        problems.append(f"table public.{t} exists after restore but is not in the dump")
    for t in sorted(exp_tables & set(actual_rows)):
        if exp_rows.get(t, 0) != actual_rows[t]:
            problems.append(
                f"public.{t}: dump has {exp_rows.get(t, 0)} rows, restored {actual_rows[t]}"
            )
    if expected["rls"] != actual_rls:
        problems.append(f"RLS-enabled tables: dump {expected['rls']}, restored {actual_rls}")
    if expected["policies"] != actual_policies:
        problems.append(f"policies: dump {expected['policies']}, restored {actual_policies}")
    if require_nonempty and exp_rows.get(require_nonempty, 0) == 0:
        problems.append(f"public.{require_nonempty} has 0 rows in the dump (empty-looking backup)")
    return problems


def _verify(dump: Path, dsn: str, require_nonempty: str | None) -> int:
    import psycopg2  # local import: `roles` must work without a DB driver

    expected = parse_dump(_lines(dump))
    conn = psycopg2.connect(dsn)
    try:
        cur = conn.cursor()
        cur.execute(
            "select c.relname, c.relrowsecurity from pg_class c join pg_namespace n "
            "on n.oid = c.relnamespace where n.nspname = 'public' and c.relkind in ('r','p')"
        )
        rels = cur.fetchall()
        actual_rows: dict[str, int] = {}
        for name, _ in rels:
            cur.execute(f'select count(*) from public."{name}"')  # noqa: S608 -- name from pg_class
            actual_rows[name] = cur.fetchone()[0]
        cur.execute("select count(*) from pg_policies where schemaname = 'public'")
        policies = cur.fetchone()[0]
        rls = sum(1 for _, on in rels if on)
    finally:
        conn.close()
    exp_rows: Counter[str] = expected["rows"]  # type: ignore[assignment]
    print(f"{'table':40} {'dump rows':>10} {'restored':>10}")
    for t in sorted(set(actual_rows) | set(expected["tables"])):  # type: ignore[arg-type]
        print(f"{t:40} {exp_rows.get(t, 0):>10} {actual_rows.get(t, '-'):>10}")
    print(
        f"RLS tables dump/restored: {expected['rls']}/{rls}; policies: {expected['policies']}/{policies}"
    )
    problems = compare(expected, actual_rows, rls, policies, require_nonempty)
    for p in problems:
        print(f"MISMATCH: {p}")
    print("RESTORE VERIFIED" if not problems else f"RESTORE FAILED ({len(problems)} problem(s))")
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("roles")
    r.add_argument("dump", type=Path)
    v = sub.add_parser("verify")
    v.add_argument("dump", type=Path)
    v.add_argument("dsn")
    v.add_argument("--require-nonempty", default="organizations")
    a = ap.parse_args()
    if a.cmd == "roles":
        print("\n".join(roles_referenced(_lines(a.dump))))
        return 0
    return _verify(a.dump, a.dsn, a.require_nonempty)


if __name__ == "__main__":
    sys.exit(main())
