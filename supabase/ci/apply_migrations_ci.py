"""Build a throwaway Postgres the way production applies migrations: mostly as the NON-superuser
review_iq_migrator, so an ownership/privilege wall fails in CI instead of in production.

Why (Session 15d): CI used to apply every migration as the `postgres` SUPERUSER with plain
`psql -f`, while production applies them through supabase/push.py as review_iq_migrator (a
non-superuser that owns the public objects). A superuser can do anything, so CI could never see
the walls production hits -- notably that a REVOKE/GRANT by a non-owner completes WITHOUT ERROR
and changes NOTHING (20260912000003's REVOKE on public.current_org_id(), owned by postgres).
This script is the shared entry point for pre-cutover-verification.yml and
schema-drift-check.yml and for tests/integration/test_push_postconditions.py.

Steps (each database is built from scratch; roles are cluster-global and created idempotently):
  1. Bootstrap the Supabase-platform roles/default privileges (supabase/ci/
     bootstrap_supabase_roles.sql) as the superuser.
  2. PHASE 1 -- as the superuser, `push.py --through LAST_SUPERUSER_MIGRATION`: every migration
     up to and including 20260801000001. EXCLUDED from migrator-apply, and why: these create
     the roles themselves (`CREATE ROLE`, `ALTER ROLE ... BYPASSRLS`, `GRANT review_iq_migrator
     TO postgres` need a superuser -- 20260726000001 and 20260801000001), and review_iq_migrator
     does not even exist until 20260801000001 runs. Still checked: push.py runs their
     postconditions.
  3. Give review_iq_migrator a CI password, then run the ownership handoff
     (20260920000003_public_objects_owned_by_migrator.sql) as the superuser, so the objects the
     early migrations created (owned by postgres) end up owned by the migrator exactly as GG's
     hand-applied production fix left them. That file is idempotent, so push.py applying it
     again in phase 3 is a verified no-op.
  4. PHASE 3 -- as review_iq_migrator (NOT a superuser), plain `push.py`: every remaining
     migration (20260801000002 onward). Any statement needing more than the migrator has, and
     any silently-ineffective GRANT/REVOKE/ALTER (caught by that file's postcondition), fails
     the build here.

Usage:
    uv run python supabase/ci/apply_migrations_ci.py [--host H] [--port P] [--dbname D]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import psycopg2
from psycopg2 import sql

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_SQL = ROOT / "supabase" / "ci" / "bootstrap_supabase_roles.sql"
MIGRATIONS_DIR = ROOT / "supabase" / "migrations"
PUSH_PY = ROOT / "supabase" / "push.py"

# Last migration that must run as a superuser (creates review_iq_migrator itself).
LAST_SUPERUSER_MIGRATION = "20260801000001_role_separation_bypassrls_remediation.sql"
OWNERSHIP_HANDOFF = "20260920000003_public_objects_owned_by_migrator.sql"

# Throwaway CI credentials (the container is destroyed with the job; same convention as the
# ci_postgres_password already in the workflows).
DEFAULT_SUPERUSER_PASSWORD = "ci_postgres_password"  # noqa: S105
MIGRATOR_PASSWORD = "ci_migrator_password"  # noqa: S105


def dsn(user: str, password: str, host: str, port: int, dbname: str) -> str:
    """A libpq URL for the throwaway database (sslmode disabled: local container)."""
    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}?sslmode=disable"


def _run_push(url: str, *args: str) -> None:
    """Run supabase/push.py as a subprocess against `url`; raise on a nonzero exit."""
    # PYTHONIOENCODING: push.py prints a non-ASCII ellipsis; keep it from crashing on a
    # non-UTF-8 console (a local Windows run), where a pipe would default to a legacy codepage.
    env = {**os.environ, "SUPABASE_DIRECT_URL": url, "PYTHONIOENCODING": "utf-8"}
    subprocess.run([sys.executable, str(PUSH_PY), *args], env=env, check=True)  # noqa: S603


def _exec_file(super_url: str, path: Path) -> None:
    """Execute a whole SQL file as the superuser, autocommit (the psql -f equivalent)."""
    conn = psycopg2.connect(super_url)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(path.read_text(encoding="utf-8"))
    finally:
        conn.close()


def build(
    host: str = "localhost",
    port: int = 5432,
    dbname: str = "postgres",
    superuser_password: str = DEFAULT_SUPERUSER_PASSWORD,
) -> str:
    """Build the migrated schema in `dbname`; return the migrator's DSN (for --verify etc.)."""
    super_url = dsn("postgres", superuser_password, host, port, dbname)
    migrator_url = dsn("review_iq_migrator", MIGRATOR_PASSWORD, host, port, dbname)

    print("== 1/4 bootstrap Supabase-platform roles and default privileges (superuser)")
    _exec_file(super_url, BOOTSTRAP_SQL)

    print(f"== 2/4 phase 1 as superuser: migrations through {LAST_SUPERUSER_MIGRATION}")
    _run_push(super_url, "--target", "ci", "--through", LAST_SUPERUSER_MIGRATION)

    print("== 3/4 migrator password + ownership handoff (superuser)")
    conn = psycopg2.connect(super_url)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("ALTER ROLE review_iq_migrator WITH PASSWORD {}").format(
                    sql.Literal(MIGRATOR_PASSWORD)
                )
            )
    finally:
        conn.close()
    _exec_file(super_url, MIGRATIONS_DIR / OWNERSHIP_HANDOFF)

    print("== 4/4 phase 3 as review_iq_migrator (NON-superuser): every remaining migration")
    _run_push(migrator_url, "--target", "ci")
    return migrator_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", default=os.environ.get("CI_PG_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CI_PG_PORT", "5432")))
    parser.add_argument("--dbname", default=os.environ.get("CI_PG_DBNAME", "postgres"))
    parser.add_argument(
        "--superuser-password",
        default=os.environ.get("CI_PG_SUPERUSER_PASSWORD", DEFAULT_SUPERUSER_PASSWORD),
    )
    args = parser.parse_args(argv)
    build(args.host, args.port, args.dbname, args.superuser_password)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
