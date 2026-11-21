"""Helpers for .github/workflows/migration-rehearsal.yml (rehearsal on a RESTORED backup).

Everything here talks to a throwaway local Postgres holding a restored copy of the nightly
backup -- never to production. Connection comes from REHEARSAL_* env vars (host/port/dbname/
user/password), refusing any host that is not local.

Subcommands:
  pending              print repo migration files absent from the restored public._migrations
                       ledger (fails if the ledger is missing or empty: a restore with no ledger
                       would make EVERY migration look pending).
  counts OUT.json      write {table: row_count} for every public base table.
  compare PRE POST     print a per-table diff; exit 1 if any table lost rows.
  undo FILE [FILE...]  for each given migration, run its UNDO_CASES (tests/integration/
                       test_push_postconditions.py) against the restored DB inside a transaction
                       that is always rolled back: baseline postcondition must PASS, the undo
                       must make it FAIL. A file with postconditions but no undo case fails.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

import psycopg2

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = ROOT / "supabase" / "migrations"
_LOCAL_HOSTS = {"localhost", "127.0.0.1"}


def connect(user: str | None = None) -> Any:
    """Connect to the restored throwaway DB; refuse anything that is not a local host."""
    host = os.environ.get("REHEARSAL_HOST", "")
    if host not in _LOCAL_HOSTS:
        raise SystemExit(f"refusing to connect: REHEARSAL_HOST must be local, got {host!r}")
    return psycopg2.connect(
        host=host,
        port=int(os.environ.get("REHEARSAL_PORT", "5432")),
        dbname=os.environ.get("REHEARSAL_DBNAME", "postgres"),
        user=user or os.environ.get("REHEARSAL_USER", "postgres"),
        password=os.environ["REHEARSAL_PASSWORD"],
        connect_timeout=15,
    )


def pending_files() -> list[str]:
    """Repo migration filenames not recorded in the restored ledger, in apply order."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public._migrations') IS NOT NULL")
        if not cur.fetchone()[0]:
            raise SystemExit("restored DB has no public._migrations ledger: restore is not usable")
        cur.execute("SELECT filename FROM public._migrations")
        applied = {r[0] for r in cur.fetchall()}
    if not applied:
        raise SystemExit("restored ledger is empty: refusing to treat every migration as pending")
    return [p.name for p in sorted(MIGRATIONS_DIR.glob("*.sql")) if p.name not in applied]


def row_counts() -> dict[str, int]:
    """Exact row count of every public base table (quoted identifiers, from the catalog)."""
    out: dict[str, int] = {}
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY 1"
        )
        for (name,) in cur.fetchall():
            cur.execute(f'SELECT count(*) FROM public."{name}"')  # noqa: S608 -- catalog name
            out[name] = cur.fetchone()[0]
    return out


def compare(pre: dict[str, int], post: dict[str, int]) -> bool:
    """Print the diff; True only if no table that existed before lost rows or vanished."""
    ok = True
    for name in sorted(set(pre) | set(post)):
        a, b = pre.get(name), post.get(name)
        flag = ""
        if b is None:
            flag, ok = "  <-- TABLE DROPPED", False
        elif a is not None and b < a:
            flag, ok = "  <-- LOST ROWS", False
        elif a is None:
            flag = "  (new table)"
        print(f"{name:40s} {a!s:>10} -> {b!s:>10}{flag}")
    return ok


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def undo(files: list[str]) -> bool:
    """Run the undo cases of `files` on the restored DB; every transaction is rolled back."""
    os.environ.setdefault("TEST_DB_HOST", "localhost")  # the test module refuses to import else
    sys.path.insert(0, str(ROOT / "supabase"))
    push = _load("push_rehearsal", ROOT / "supabase" / "push.py")
    tests = _load(
        "test_push_postconditions_rehearsal",
        ROOT / "tests" / "integration" / "test_push_postconditions.py",
    )
    ok = True
    for fname in files:
        text = (MIGRATIONS_DIR / fname).read_text(encoding="utf-8")
        pcs = {pc.name: pc for pc in push.parse_postconditions(text)}
        cases = [c for c in tests.UNDO_CASES if c[0] == fname]
        if pcs and not cases:
            print(f"FAIL {fname}: has postconditions but no UNDO_CASES entry")
            ok = False
        for _, pc_name, undo_sql in cases:
            conn = connect()
            try:
                cur = conn.cursor()
                base = push.evaluate_postcondition(cur, pcs[pc_name])
                cur.execute(undo_sql)
                after = push.evaluate_postcondition(cur, pcs[pc_name])
                good = base.status == "PASS" and after.status == "FAIL"
                print(
                    f"{'ok  ' if good else 'FAIL'} {fname} {pc_name}: "
                    f"before={base.status} after-undo={after.status}"
                )
                ok = ok and good
            finally:
                conn.rollback()  # the rehearsal never keeps an undo
                conn.close()
    return ok


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "pending":
        print("\n".join(pending_files()))
        return 0
    if cmd == "counts" and len(argv) == 2:
        Path(argv[1]).write_text(json.dumps(row_counts(), indent=2), encoding="utf-8")
        return 0
    if cmd == "compare" and len(argv) == 3:
        pre = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        post = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        return 0 if compare(pre, post) else 1
    if cmd == "undo" and len(argv) >= 2:
        return 0 if undo(argv[1:]) else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
