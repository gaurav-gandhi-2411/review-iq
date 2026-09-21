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
    uv run python supabase/push.py --verify         # READ-ONLY audit: run every ledgered
                                                      # file's postconditions, print a table,
                                                      # exit 1 on any FAIL / NO_POSTCONDITION
    uv run python supabase/push.py --target ci      # `prod` (default) or `ci`: which
                                                      # `@scope:` postconditions apply
    uv run python supabase/push.py --through FILE   # apply only pending files <= FILE (the CI
                                                      # hybrid: supabase/ci/apply_migrations_ci.py)

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

Postconditions (Session 15d): the ledger used to record "this file's SQL ran without error",
which is not the same as "this file's effect is true". A non-owner REVOKE/GRANT completes
without error and changes nothing (20260912000003's REVOKE on public.current_org_id(), owned
by postgres, run as review_iq_migrator) -- the ledger would have said applied while production
kept the grant. Every migration therefore declares machine-readable postconditions (grammar in
supabase/postconditions.py's docstring): `-- @postcondition: <name>` followed by
`-- SQL: <single SELECT returning one boolean>`. Behavior:

  * apply: after a file executes and BEFORE its ledger row is inserted, in the SAME
    transaction, every applicable postcondition runs. Any FALSE / NULL / error / non-boolean /
    not-exactly-one-row -> ROLLBACK, no ledger row, exit 1 naming file + postcondition + the
    returned value. A file with no postcondition is REFUSED (before anything is applied)
    unless it is in supabase/postconditions.py's ALLOWLIST with a reason.
  * --mark-applied-all: records nothing for a file whose postconditions do not hold now.
    --force does NOT override a postcondition failure (it only ever covered the older
    object-existence heuristics below).
  * --verify: read-only (READ ONLY session, SAVEPOINT per condition) audit of an existing
    database, safe to run against production as the migrator. `@scope: prod-only|ci-only`
    conditions are skipped on the other --target and reported as SKIPPED(scope).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from postconditions import (
    ALLOWLIST,
    Postcondition,
    PostconditionError,
    applies_to,
    find_transaction_control,
    parse_postconditions,
)

ROOT = Path(__file__).parents[1]
load_dotenv(ROOT / ".env")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_ENSURE_LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS public._migrations (
    filename text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON public._migrations FROM PUBLIC, anon, authenticated;
"""
# Issue #186: this table was created with no explicit grants, so Postgres's default
# PUBLIC-inherited privileges silently gave anon and authenticated SELECT on it -- caught by
# the "ACL Exposure Check" workflow, not by schema-drift-check.yml (which only knows about
# migration-file-managed objects, not this script's own bookkeeping table). The REVOKE above
# runs every invocation (idempotent, no-op once already revoked) so this can't silently
# regress if the table is ever dropped and recreated, and is the pattern for any future
# tooling table this script creates outside supabase/migrations/.

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


@dataclass(frozen=True)
class FilePlan:
    """What push.py will do about one migration file's postconditions.

    kind: "ok" (>= 1 well-formed postcondition), "allowlisted" (no postcondition, reason in
    ALLOWLIST), "missing" (none and not allowlisted -- refused) or "malformed" (unparseable,
    or the file carries its own COMMIT -- refused). `detail` explains "missing"/"malformed"/
    "allowlisted".
    """

    kind: str
    postconditions: tuple[Postcondition, ...] = ()
    detail: str = ""


def plan_file(filename: str, sql_text: str) -> FilePlan:
    """Parse `sql_text`'s postconditions and decide, fail-closed, whether the file may run."""
    control = find_transaction_control(sql_text)
    if control:
        return FilePlan(
            "malformed",
            detail=(
                f"contains its own transaction control ({control!r}); push.py runs file + "
                "postconditions + ledger row as one transaction, and an inner COMMIT would "
                "make the rollback-on-failure guarantee false"
            ),
        )
    try:
        pcs = parse_postconditions(sql_text)
    except PostconditionError as exc:
        return FilePlan("malformed", detail=str(exc))
    if pcs:
        return FilePlan("ok", tuple(pcs))
    if filename in ALLOWLIST:
        return FilePlan("allowlisted", detail=ALLOWLIST[filename])
    return FilePlan(
        "missing",
        detail=(
            "declares no '-- @postcondition:' block and is not in supabase/postconditions.py "
            "ALLOWLIST; every migration must state a machine-checkable effect"
        ),
    )


@dataclass(frozen=True)
class PcResult:
    """Outcome of one postcondition: status is PASS, FAIL or SKIPPED(scope)."""

    name: str
    status: str
    detail: str = ""


def evaluate_postcondition(cur: psycopg2.extensions.cursor, pc: Postcondition) -> PcResult:
    """Run one postcondition; PASS only for exactly one row, one column, value True.

    Runs inside a SAVEPOINT that is always rolled back, so (a) a SQL error does not poison the
    caller's transaction and (b) nothing the SELECT did can survive into the ledgered
    transaction. NULL, False, zero/many rows, a non-boolean and any exception are all FAIL.
    """
    cur.execute("SAVEPOINT pc_eval")
    try:
        cur.execute(pc.sql)
        rows = cur.fetchall()
        if len(rows) != 1 or len(rows[0]) != 1:
            return PcResult(pc.name, "FAIL", f"expected exactly 1 row x 1 column, got {rows!r}")
        value = rows[0][0]
        if value is True:
            return PcResult(pc.name, "PASS")
        return PcResult(pc.name, "FAIL", f"returned {value!r} (must be exactly true)")
    except Exception as exc:  # noqa: BLE001 -- any error is a failed postcondition, by design
        return PcResult(pc.name, "FAIL", f"errored: {type(exc).__name__}: {str(exc).strip()}")
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT pc_eval")
        cur.execute("RELEASE SAVEPOINT pc_eval")


def run_postconditions(
    cur: psycopg2.extensions.cursor, pcs: tuple[Postcondition, ...], target: str
) -> list[PcResult]:
    """Evaluate every postcondition applicable to `target`; out-of-scope ones are SKIPPED."""
    results: list[PcResult] = []
    for pc in pcs:
        if applies_to(pc, target):
            results.append(evaluate_postcondition(cur, pc))
        else:
            results.append(PcResult(pc.name, "SKIPPED(scope)", f"{pc.scope}: {pc.scope_reason}"))
    return results


def _refusal_lines(filename: str, plan: FilePlan) -> list[str]:
    return [f"  REFUSED {filename}: {plan.kind}: {plan.detail}"]


def _apply_pending(
    conn: psycopg2.extensions.connection, pending: list[Path], target: str
) -> tuple[bool, int]:
    """Apply `pending` in order; each file = execute + postconditions + ledger row, atomically.

    Returns (ok, applied_count). Files lacking usable postconditions are refused up front,
    before anything is applied. On the first failing postcondition the transaction is rolled
    back (no ledger row) and (False, n) is returned; earlier files stay committed.
    """
    texts = {p.name: p.read_text(encoding="utf-8") for p in pending}
    plans = {name: plan_file(name, text) for name, text in texts.items()}
    bad = [(n, pl) for n, pl in plans.items() if pl.kind in ("missing", "malformed")]
    if bad:
        for name, plan in bad:
            for line in _refusal_lines(name, plan):
                print(line, file=sys.stderr)
        print(
            f"\n{len(bad)} pending file(s) lack usable postconditions -- nothing applied "
            "(fail closed).",
            file=sys.stderr,
        )
        return False, 0

    applied = 0
    for path in pending:
        plan = plans[path.name]
        print(f"  Applying {path.name} …", end=" ", flush=True)
        with conn.cursor() as cur:
            cur.execute(texts[path.name])
            failed = [
                r
                for r in run_postconditions(cur, plan.postconditions, target)
                if r.status == "FAIL"
            ]
            if failed:
                conn.rollback()
                print("POSTCONDITION FAILED")
                for r in failed:
                    print(
                        f"POSTCONDITION FAILED: {path.name} :: {r.name} -- {r.detail}. "
                        "Rolled back; NOT recorded in public._migrations.",
                        file=sys.stderr,
                    )
                return False, applied
            cur.execute("INSERT INTO public._migrations (filename) VALUES (%s)", (path.name,))
        conn.commit()
        applied += 1
        note = " (allowlisted, no postcondition)" if plan.kind == "allowlisted" else ""
        print(f"OK{note}")
    return True, applied


def _verify(conn: psycopg2.extensions.connection, migration_files: list[Path], target: str) -> int:
    """Read-only audit: run every ledgered file's postconditions; return the exit code.

    Prints `file | postcondition | status`. Exit 1 on any FAIL or NO_POSTCONDITION (a
    malformed block counts as FAIL). Files not in the ledger and ledger rows with no file are
    reported but do not by themselves fail the run.
    """
    ledger = _applied_filenames_readonly(conn)
    by_name = {p.name: p for p in migration_files}
    rows: list[tuple[str, str, str, str]] = []
    exit_code = 0
    with conn.cursor() as cur:
        for name in sorted(ledger):
            path = by_name.get(name)
            if path is None:
                rows.append((name, "-", "LEDGER_ROW_NO_FILE", "ledger row has no file on disk"))
                continue
            plan = plan_file(name, path.read_text(encoding="utf-8"))
            if plan.kind == "allowlisted":
                rows.append((name, "-", "ALLOWLISTED", plan.detail))
            elif plan.kind == "missing":
                rows.append((name, "-", "NO_POSTCONDITION", plan.detail))
                exit_code = 1
            elif plan.kind == "malformed":
                rows.append((name, "-", "FAIL", f"malformed postcondition block: {plan.detail}"))
                exit_code = 1
            else:
                for r in run_postconditions(cur, plan.postconditions, target):
                    rows.append((name, r.name, r.status, r.detail))
                    if r.status == "FAIL":
                        exit_code = 1
        conn.rollback()
    for name in sorted(by_name):
        if name not in ledger:
            rows.append((name, "-", "NOT_IN_LEDGER", "file on disk, no ledger row (not verified)"))

    width_f = max([len("file")] + [len(r[0]) for r in rows])
    width_p = max([len("postcondition")] + [len(r[1]) for r in rows])
    print(f"{'file':<{width_f}} | {'postcondition':<{width_p}} | status")
    print(f"{'-' * width_f}-+-{'-' * width_p}-+-------")
    for name, pc_name, status, detail in rows:
        suffix = f"  -- {detail}" if detail and status != "PASS" else ""
        print(f"{name:<{width_f}} | {pc_name:<{width_p}} | {status}{suffix}")
    counts: dict[str, int] = {}
    for _, _, status, _ in rows:
        counts[status] = counts.get(status, 0) + 1
    print("\nsummary: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"target={target}; exit {exit_code}")
    return exit_code


def main(argv: list[str] | None = None) -> None:
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
    parser.add_argument(
        "--fail-on-pending",
        action="store_true",
        help="With --dry-run: exit 1 if any pending file is genuinely unapplied (objects "
        "missing or grant mismatch found) -- for a scheduled drift-detection gate. Does NOT "
        "fail on the 'objects already exist, needs --mark-applied-all' or 'no recognizable "
        "objects' cases -- those are ledger bookkeeping gaps, not a genuine drift between "
        "what's merged and what's live. Item 248: a merged migration sitting unapplied "
        "against production for 2 days, undetected, is exactly the gap this flag closes.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Read-only audit (READ ONLY session, safe against production as the migrator): "
        "run every ledgered file's postconditions and print a file | postcondition | status "
        "table (PASS/FAIL/NO_POSTCONDITION/SKIPPED(scope)); also lists files not in the "
        "ledger and ledger rows with no file. Exit 1 on any FAIL or NO_POSTCONDITION.",
    )
    parser.add_argument(
        "--target",
        choices=("prod", "ci"),
        default="prod",
        help="Which `-- @scope:` postconditions apply: `prod` (default) skips ci-only ones, "
        "`ci` skips prod-only ones. Unscoped postconditions always run.",
    )
    parser.add_argument(
        "--through",
        metavar="FILENAME",
        help="Apply only pending files whose name is <= FILENAME (must be a real migration "
        "file). For the CI hybrid, which applies role-creating migrations as a superuser and "
        "the rest as the non-superuser migrator (supabase/ci/apply_migrations_ci.py).",
    )
    args = parser.parse_args(argv)
    if args.verify and (args.dry_run or args.mark_applied_all or args.through):
        parser.error("--verify is read-only and cannot be combined with other actions")
    if args.through and (args.dry_run or args.mark_applied_all):
        parser.error("--through only applies to a normal apply run")

    direct_url = os.environ["SUPABASE_DIRECT_URL"]
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not migration_files:
        print("No migration files found.", file=sys.stderr)
        sys.exit(1)
    if args.through and args.through not in {p.name for p in migration_files}:
        print(f"--through {args.through!r} is not a file in {MIGRATIONS_DIR}", file=sys.stderr)
        sys.exit(2)

    print("Connecting via SUPABASE_DIRECT_URL (port 5432) …")
    conn = psycopg2.connect(direct_url)
    conn.autocommit = False

    try:
        if args.verify:
            conn.set_session(readonly=True)
            code = _verify(conn, migration_files, args.target)
            if code:
                sys.exit(code)
            return

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
            genuinely_unapplied = 0
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
                        genuinely_unapplied += 1
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
            if args.fail_on_pending and genuinely_unapplied:
                print(
                    f"\n{genuinely_unapplied} file(s) merged but not applied to this "
                    "target -- failing (--fail-on-pending)."
                )
                sys.exit(1)
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
                    # Postconditions are checked FIRST and are NOT overridable by --force:
                    # recording a file whose stated effect is false is the exact decorative-
                    # control failure this feature exists to stop.
                    plan = plan_file(path.name, sql)
                    if plan.kind in ("missing", "malformed"):
                        pc_failures = [f"{plan.kind}: {plan.detail}"]
                    elif plan.kind == "ok":
                        pc_failures = [
                            f"{r.name}: {r.detail}"
                            for r in run_postconditions(cur, plan.postconditions, args.target)
                            if r.status == "FAIL"
                        ]
                    else:
                        pc_failures = []
                    if pc_failures:
                        refused += 1
                        print(f"  REFUSED (postcondition failed, not marked): {path.name}")
                        for failure in pc_failures:
                            print(f"      {failure}")
                        continue
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
            print(f"\n{marked} file(s) marked applied, {refused} refused.")
            if refused:
                print(
                    "--force only overrides the older missing-object/grant-state heuristics; "
                    "it never overrides a failed postcondition. Investigate why the stated "
                    "effect is not true before recording anything."
                )
                sys.exit(1)
            return

        if args.through:
            pending = [p for p in pending if p.name <= args.through]

        if not pending:
            print("\nNothing to apply -- every migration file is already recorded.")
            return

        ok, applied = _apply_pending(conn, pending, args.target)
        if not ok:
            print(f"\n{applied} migration(s) applied before the failure.", file=sys.stderr)
            sys.exit(1)

        print(f"\n{applied} migration(s) applied.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
