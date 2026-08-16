"""Apply all migrations in supabase/migrations/ to the live Supabase DB.

Usage:
    uv run python supabase/push.py                 # apply every not-yet-applied file
    uv run python supabase/push.py --dry-run        # print what WOULD apply, whether each
                                                      # pending file's objects already exist
                                                      # in the target -- read-only, no writes,
                                                      # no ledger rows -- and apply nothing
    uv run python supabase/push.py --mark-applied-all   # record every file as applied
                                                          # WITHOUT running its SQL --
                                                          # backfill for a database that
                                                          # already has these migrations'
                                                          # content, applied out-of-band
                                                          # (see the module docstring below
                                                          # for why this exists)

Reads credentials from .env (direct connection, port 5432).

Ledger (Item 172, 2026-08-16): before this pass, this script had no record of what had
already been applied -- it re-ran every file, every invocation, relying entirely on each
file's own idempotency (IF NOT EXISTS / CREATE OR REPLACE / DROP IF EXISTS). That was safe
for every file EXCEPT one: supabase/migrations/20260801000001_role_separation_bypassrls_
remediation.sql used to bundle five always-safe statements with one sequencing-sensitive
one (revoking review_iq_app's BYPASSRLS) that must not fire until specific application code
is deployed. Re-running this script for an unrelated reason (e.g. syncing in a newly added
out-of-band-capture migration) would have silently re-applied that statement too, with no
warning. Fixed two ways, together: (1) the sequencing-sensitive statement itself moved to
supabase/cutover/, outside this script's glob entirely -- applying it is now a separate,
explicit act, never a side effect of running this script; (2) this script now tracks what
it has applied in public._migrations, so re-running it is a genuine no-op for anything
already recorded, rather than "safe by accident" via each file's own idempotency.

Migrations are applied in filename order. Each file is still expected to be idempotent
(IF NOT EXISTS / CREATE OR REPLACE / DROP IF EXISTS) as defense in depth, but the ledger
is now the primary mechanism deciding what runs.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).parents[1]
load_dotenv(ROOT / ".env")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_ENSURE_LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS public._migrations (
    filename text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
"""

# --mark-applied-all backfill verification (Item 176d): a blank "trust the flag" backfill
# can silently and PERMANENTLY hide a migration that was never actually applied -- demonstrated
# directly (Item 176c) by skipping 20260710235958_capture_extraction_costs.sql on a throwaway
# container, running --mark-applied-all, and confirming it was marked applied anyway despite
# public.extraction_costs genuinely not existing. This is deliberately NOT a general SQL-effect
# verifier (that's real overengineering for what a handful of regexes covers) -- it extracts the
# one or two primary named objects each file's own CREATE/ALTER statements are recognizable by,
# and checks those specific objects exist. Catches "this file was never applied at all" (the
# demonstrated failure mode); does not catch "this file was applied but a later statement in it
# silently failed" or verify column-level correctness -- named explicitly as the boundary of what
# this checks, not implied to be exhaustive.
_OBJECT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("table", re.compile(r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+public\.(\w+)", re.IGNORECASE)),
    (
        "function",
        re.compile(r"CREATE(?:\s+OR REPLACE)?\s+FUNCTION\s+public\.(\w+)", re.IGNORECASE),
    ),
    ("role", re.compile(r"\bCREATE ROLE\s+(\w+)", re.IGNORECASE)),
    ("constraint", re.compile(r"\bADD CONSTRAINT\s+(\w+)", re.IGNORECASE)),
]

_EXISTENCE_SQL = {
    "table": "SELECT to_regclass('public.' || %s) IS NOT NULL",
    "function": "SELECT EXISTS(SELECT 1 FROM pg_proc WHERE proname = %s)",
    "role": "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname = %s)",
    "constraint": "SELECT EXISTS(SELECT 1 FROM pg_constraint WHERE conname = %s)",
}

# Item 203c/204: a file whose only statements are REVOKE ALL / GRANT (a grant-narrowing
# migration, e.g. 20260817000001_extraction_costs_grant_narrowing.sql) has no CREATE
# TABLE/FUNCTION/ROLE/ADD CONSTRAINT for _expected_objects above to find -- confirmed
# directly on a container that such a file falls into the "no recognizable objects"
# UNVERIFIED branch and --mark-applied-all marks it applied with zero verification.
# That is a real, demonstrated gap, same failure class as Item 176c: a file that was
# never actually run gets silently recorded as if it had been. A REVOKE ALL ... FROM
# <role> followed by a GRANT ... TO <role> is describing an EXACT target privilege
# state for that (table, role) pair, not just "an object exists" -- checkable precisely
# via information_schema.role_table_grants, not just a boolean existence check.
_REVOKE_ALL_PATTERN = re.compile(r"REVOKE ALL ON public\.(\w+)\s+FROM\s+(\w+)", re.IGNORECASE)
_GRANT_PATTERN = re.compile(r"GRANT\s+([\w\s,]+?)\s+ON public\.(\w+)\s+TO\s+(\w+)", re.IGNORECASE)


def _expected_grant_states(sql: str) -> list[tuple[str, str, frozenset[str]]]:
    """Return (table, grantee, expected_privileges) for every (table, grantee) pair the
    file explicitly resets via REVOKE ALL -- expected_privileges is the union of any
    GRANT ... TO that same grantee for that same table found afterward (empty set if
    none -- REVOKE ALL with no matching GRANT means "expect nothing granted").

    PUBLIC is skipped -- information_schema.role_table_grants keys grantee by literal
    role name and PUBLIC-grantee semantics differ from a named role's; not the focus of
    this check, which exists for anon/authenticated-style narrowing.
    """
    code = _strip_sql_line_comments(sql)
    reset_pairs: set[tuple[str, str]] = set()
    for table, grantee in _REVOKE_ALL_PATTERN.findall(code):
        if grantee.upper() != "PUBLIC":
            reset_pairs.add((table, grantee))

    granted: dict[tuple[str, str], set[str]] = {}
    for privs, table, grantee in _GRANT_PATTERN.findall(code):
        if grantee.upper() == "PUBLIC":
            continue
        priv_set = {p.strip().upper() for p in privs.split(",")}
        granted.setdefault((table, grantee), set()).update(priv_set)

    return [
        (table, grantee, frozenset(granted.get((table, grantee), set())))
        for table, grantee in reset_pairs
    ]


def _grant_mismatches(
    cur: psycopg2.extensions.cursor, expected: list[tuple[str, str, frozenset[str]]]
) -> list[tuple[str, str, frozenset[str], frozenset[str]]]:
    mismatches = []
    for table, grantee, expected_privs in expected:
        cur.execute(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE table_schema='public' AND table_name=%s AND grantee=%s",
            (table, grantee),
        )
        actual = frozenset(row[0] for row in cur.fetchall())
        if actual != expected_privs:
            mismatches.append((table, grantee, expected_privs, actual))
    return mismatches


def _strip_sql_line_comments(sql: str) -> str:
    """Strip `-- ...` line comments before object-name extraction.

    Self-caught while testing (Item 176): the raw-SQL regexes below matched literal
    English inside comments like "-- ADD CONSTRAINT does not support IF NOT EXISTS",
    extracting `does` as a fake constraint name and refusing to mark an otherwise-fine
    file. None of this repo's migrations put `--` inside a string literal, so a plain
    per-line strip is sufficient here -- not a general SQL comment parser.
    """
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def _expected_objects(sql: str) -> list[tuple[str, str]]:
    code = _strip_sql_line_comments(sql)
    found: list[tuple[str, str]] = []
    for kind, pattern in _OBJECT_PATTERNS:
        found.extend((kind, name) for name in pattern.findall(code))
    return found


def _missing_objects(
    cur: psycopg2.extensions.cursor, expected: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    missing = []
    for kind, name in expected:
        cur.execute(_EXISTENCE_SQL[kind], (name,))
        (exists,) = cur.fetchone()
        if not exists:
            missing.append((kind, name))
    return missing


def _applied_filenames(conn: psycopg2.extensions.connection) -> set[str]:
    """Ensures the ledger table exists (CREATE TABLE IF NOT EXISTS), then reads it.

    Only called from write paths (normal apply, --mark-applied-all) -- --dry-run uses
    _applied_filenames_readonly below instead, which never touches schema."""
    with conn.cursor() as cur:
        cur.execute(_ENSURE_LEDGER_SQL)
        conn.commit()
        cur.execute("SELECT filename FROM public._migrations")
        return {row[0] for row in cur.fetchall()}


def _applied_filenames_readonly(conn: psycopg2.extensions.connection) -> set[str]:
    """Item 181: true read-only equivalent for --dry-run -- never creates the ledger table.

    If the table doesn't exist yet (e.g. checking production before ever backfilling),
    treats that as "nothing recorded" rather than creating it as a side effect of a
    read-only command."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public._migrations') IS NOT NULL")
        (table_exists,) = cur.fetchone()
        if not table_exists:
            return set()
        cur.execute("SELECT filename FROM public._migrations")
        return {row[0] for row in cur.fetchall()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print which files would apply against SUPABASE_DIRECT_URL's target; apply nothing.",
    )
    parser.add_argument(
        "--mark-applied-all",
        action="store_true",
        help="Record every migration file in the ledger without running its SQL "
        "(backfill for a database whose content already matches these files, applied "
        "out-of-band). Refuses to mark a file whose primary CREATE TABLE/FUNCTION/ROLE or "
        "ADD CONSTRAINT object doesn't actually exist, or whose REVOKE ALL + GRANT "
        "target privilege state doesn't match reality -- pass --force to override.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="With --mark-applied-all: mark files anyway even if their expected objects "
        "are missing. Only use this after independently confirming why -- e.g. a file "
        "whose statements are entirely GRANT/REVOKE with nothing this script's checks can "
        "recognize.",
    )
    args = parser.parse_args()

    direct_url = os.environ["SUPABASE_DIRECT_URL"]
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not migration_files:
        print("No migration files found.", file=sys.stderr)
        sys.exit(1)

    print("Connecting via SUPABASE_DIRECT_URL (port 5432) …")
    conn = psycopg2.connect(direct_url)
    conn.autocommit = False

    try:
        if args.dry_run:
            already_applied = _applied_filenames_readonly(conn)
            pending = [p for p in migration_files if p.name not in already_applied]
            # Item 181: also report whether each pending file's expected objects already
            # exist in the target -- read-only (SELECT only, no INSERT, no commit needed),
            # so this is safe to run against production before deciding whether a file is
            # genuinely unapplied (WOULD APPLY, objects missing) or was applied out-of-band
            # and just needs a ledger backfill (WOULD APPLY, but objects ALREADY EXIST --
            # a signal to use --mark-applied-all for that file, not a normal apply).
            print(f"\n{len(already_applied)} already applied, {len(pending)} would apply:")
            with conn.cursor() as cur:
                for path in pending:
                    sql = path.read_text(encoding="utf-8")
                    expected = _expected_objects(sql)
                    missing = _missing_objects(cur, expected)
                    grant_states = _expected_grant_states(sql)
                    grant_mismatches = _grant_mismatches(cur, grant_states)
                    if not expected and not grant_states:
                        print(f"  WOULD APPLY (no recognizable objects to check): {path.name}")
                    elif missing or grant_mismatches:
                        print(
                            f"  WOULD APPLY (objects missing -- genuinely unapplied): {path.name}"
                        )
                        for kind, name in missing:
                            print(f"      missing {kind}: {name}")
                        for table, grantee, exp_privs, actual in grant_mismatches:
                            print(
                                f"      grant mismatch on {table} for {grantee}: "
                                f"expected {sorted(exp_privs)}, actual {sorted(actual)}"
                            )
                    else:
                        print(
                            f"  WOULD APPLY, BUT OBJECTS ALREADY EXIST -- likely applied "
                            f"out-of-band, consider --mark-applied-all instead: {path.name}"
                        )
            for path in migration_files:
                if path.name in already_applied:
                    print(f"  (already applied, skip): {path.name}")
            return

        already_applied = _applied_filenames(conn)
        pending = [p for p in migration_files if p.name not in already_applied]

        if args.mark_applied_all:
            marked, refused = 0, 0
            with conn.cursor() as cur:
                for path in pending:
                    sql = path.read_text(encoding="utf-8")
                    expected = _expected_objects(sql)
                    missing = _missing_objects(cur, expected)
                    grant_states = _expected_grant_states(sql)
                    grant_mismatches = _grant_mismatches(cur, grant_states)
                    if (missing or grant_mismatches) and not args.force:
                        refused += 1
                        print(f"  REFUSED (objects missing, not marked): {path.name}")
                        for kind, name in missing:
                            print(f"      missing {kind}: {name}")
                        for table, grantee, exp_privs, actual in grant_mismatches:
                            print(
                                f"      grant mismatch on {table} for {grantee}: "
                                f"expected {sorted(exp_privs)}, actual {sorted(actual)}"
                            )
                        continue
                    if not expected and not grant_states:
                        print(
                            f"  Marked applied (SQL NOT run, UNVERIFIED -- no recognizable "
                            f"CREATE TABLE/FUNCTION/ROLE/ADD CONSTRAINT or REVOKE ALL+GRANT "
                            f"found): {path.name}"
                        )
                    else:
                        print(f"  Marked applied (SQL NOT run, verified): {path.name}")
                    cur.execute(
                        "INSERT INTO public._migrations (filename) VALUES (%s) "
                        "ON CONFLICT (filename) DO NOTHING",
                        (path.name,),
                    )
                    marked += 1
            conn.commit()
            print(f"\n{marked} file(s) marked applied, {refused} refused (missing objects).")
            if refused:
                print(
                    "Re-run with --force only after confirming why those objects are "
                    "missing -- do not use --force to silence this without checking."
                )
                sys.exit(1)
            return

        if not pending:
            print("\nNothing to apply -- every migration file is already recorded.")
            return

        for path in pending:
            sql = path.read_text(encoding="utf-8")
            print(f"  Applying {path.name} …", end=" ", flush=True)
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute("INSERT INTO public._migrations (filename) VALUES (%s)", (path.name,))
            conn.commit()
            print("OK")

        print(f"\n{len(pending)} migration(s) applied.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
