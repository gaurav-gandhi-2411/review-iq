"""Apply all migrations in supabase/migrations/ to the live Supabase DB.

Usage:
    uv run python supabase/push.py                 # apply every not-yet-applied file
    uv run python supabase/push.py --dry-run        # print what WOULD apply, apply nothing
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
    with conn.cursor() as cur:
        cur.execute(_ENSURE_LEDGER_SQL)
        conn.commit()
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
        "ADD CONSTRAINT object doesn't actually exist -- pass --force to override.",
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
        already_applied = _applied_filenames(conn)
        pending = [p for p in migration_files if p.name not in already_applied]

        if args.dry_run:
            print(f"\n{len(already_applied)} already applied, {len(pending)} would apply:")
            for path in pending:
                print(f"  WOULD APPLY: {path.name}")
            for path in migration_files:
                if path.name in already_applied:
                    print(f"  (already applied, skip): {path.name}")
            return

        if args.mark_applied_all:
            marked, refused = 0, 0
            with conn.cursor() as cur:
                for path in pending:
                    sql = path.read_text(encoding="utf-8")
                    expected = _expected_objects(sql)
                    missing = _missing_objects(cur, expected)
                    if missing and not args.force:
                        refused += 1
                        print(f"  REFUSED (objects missing, not marked): {path.name}")
                        for kind, name in missing:
                            print(f"      missing {kind}: {name}")
                        continue
                    if not expected:
                        print(
                            f"  Marked applied (SQL NOT run, UNVERIFIED -- no recognizable "
                            f"CREATE TABLE/FUNCTION/ROLE or ADD CONSTRAINT found): {path.name}"
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
