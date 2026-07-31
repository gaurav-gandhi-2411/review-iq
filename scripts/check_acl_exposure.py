"""Standing audit: catch the Supabase default-ACL exposure class before it ships again.

Wave 2 P1. The Wave 1 S0 finding (two SECURITY DEFINER webhook org-resolution functions
inheriting EXECUTE on anon/authenticated via Supabase's schema-level default ACLs,
letting any logged-in customer -- or the public anon key -- call a cross-tenant lookup
directly via PostgREST) is a RECURRING CLASS, not a one-off: any future table, view, or
SECURITY DEFINER function created in the `public` schema by a role whose default ACLs
grant anon/authenticated privileges automatically inherits the same exposure unless
someone remembers to REVOKE it. This script makes "someone remembers" a checked
invariant instead of a hope.

What it checks, against the LIVE schema (not just migration-file text -- catches
objects created out-of-band too, e.g. `public.quota_requests`, which predates any
tracked migration):

  1. SECURITY DEFINER functions in `public`: anon/authenticated must have NO EXECUTE
     privilege, UNLESS the function is on the explicit ALLOWLIST below with a stated
     reason. This is the exact bug class -- these functions run with their OWNER's
     privileges regardless of caller, so an unintended grant is a full bypass, not a
     degraded-but-contained exposure the way an RLS-protected table's default grant
     would be.

  2. Tables and views in `public`: either (a) row-level security is enabled AND an
     explicit anon-deny policy exists (this repo's own established `<table>_anon_deny`
     pattern, `FOR ALL TO anon USING (false)`), or (b) anon/authenticated have been
     explicitly REVOKEd. A table with RLS enabled but no policies at all still passes
     Postgres's own default-deny, but this script does not special-case that: an
     anon-deny policy or an explicit revoke must be visible in the grants themselves,
     not inferred from "no policy exists yet."

ALLOWLIST -- functions safe to be broadly executable, with why:

  `current_org_id`: called BY the RLS policies themselves (`USING (org_id =
  current_org_id())`) to resolve the tenant context from `app.current_org_id`/JWT
  claims. It reads only a session GUC/JWT claim -- no table access, no cross-tenant
  data exposure -- and EXECUTE by `authenticated` is REQUIRED for RLS to function at
  all (revoking it would break every authenticated query, not just close a gap). This
  is the same "explicit justification comment" pattern this repo already uses for
  documented cross-org exceptions in `storage_pg.py` (e.g.
  `list_orgs_with_dated_extractions_pg`), applied here at the ACL layer.

Usage:
    uv run python scripts/check_acl_exposure.py            # human-readable report
    uv run python scripts/check_acl_exposure.py --ci        # exit 1 on any finding, terse

Requires SUPABASE_DIRECT_URL (read-only session -- never writes, never should).
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg2
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

# Function name -> justification. Every entry here is a DELIBERATE, reviewed exception,
# not a way to silence a failing check -- adding an entry without a real justification
# defeats the entire point of this script.
_SECURITY_DEFINER_ALLOWLIST: dict[str, str] = {
    "current_org_id": (
        "Called by RLS policies themselves to resolve tenant context from a session "
        "GUC/JWT claim. No table access, no cross-tenant data exposure. EXECUTE by "
        "authenticated is REQUIRED for RLS to function -- revoking it breaks every "
        "authenticated query, not just this one function."
    ),
}


def _connect() -> psycopg2.extensions.connection:
    dsn = os.environ["SUPABASE_DIRECT_URL"]
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute("SET default_transaction_read_only = on")
    conn.commit()
    return conn


def _security_definer_functions(cur: psycopg2.extensions.cursor) -> list[str]:
    cur.execute("""
        SELECT p.proname
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.prosecdef = true
        ORDER BY p.proname
    """)
    return [row[0] for row in cur.fetchall()]


def _function_grantees(cur: psycopg2.extensions.cursor, func_name: str) -> set[str]:
    cur.execute(
        "SELECT grantee FROM information_schema.routine_privileges "
        "WHERE routine_schema = 'public' AND routine_name = %s AND privilege_type = 'EXECUTE'",
        (func_name,),
    )
    return {row[0] for row in cur.fetchall()}


def _tables_and_views(cur: psycopg2.extensions.cursor) -> list[tuple[str, bool]]:
    """Returns (relname, relrowsecurity) for every ordinary table and view in public."""
    cur.execute("""
        SELECT c.relname, c.relrowsecurity
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v')
        ORDER BY c.relname
    """)
    return [(row[0], row[1]) for row in cur.fetchall()]


def _has_anon_deny_policy(cur: psycopg2.extensions.cursor, table_name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM pg_policies "
        "WHERE schemaname = 'public' AND tablename = %s AND 'anon' = ANY(roles) "
        "AND qual = 'false'",
        (table_name,),
    )
    return cur.fetchone() is not None


def _table_grantees(cur: psycopg2.extensions.cursor, table_name: str, role: str) -> bool:
    """True if `role` has ANY privilege on this table."""
    cur.execute(
        "SELECT 1 FROM information_schema.role_table_grants "
        "WHERE table_schema = 'public' AND table_name = %s AND grantee = %s LIMIT 1",
        (table_name, role),
    )
    return cur.fetchone() is not None


def run_audit() -> list[str]:
    """Returns a list of finding strings; empty list = clean."""
    findings: list[str] = []
    conn = _connect()
    try:
        cur = conn.cursor()

        for func_name in _security_definer_functions(cur):
            if func_name in _SECURITY_DEFINER_ALLOWLIST:
                continue
            grantees = _function_grantees(cur, func_name)
            exposed = grantees & {"anon", "authenticated"}
            if exposed:
                findings.append(
                    f"SECURITY DEFINER function 'public.{func_name}' grants EXECUTE to "
                    f"{sorted(exposed)} -- not on the allowlist. Add an explicit "
                    f"REVOKE ... FROM anon, authenticated (and GRANT to only the "
                    f"intended caller), or add a justified allowlist entry if this "
                    f"grant is genuinely required (see _SECURITY_DEFINER_ALLOWLIST)."
                )

        for table_name, rls_enabled in _tables_and_views(cur):
            if not rls_enabled:
                # No RLS at all -- explicit revoke is the only possible protection.
                anon_has_access = _table_grantees(cur, table_name, "anon")
                if anon_has_access:
                    findings.append(
                        f"Table/view 'public.{table_name}' has RLS disabled AND anon "
                        f"holds a grant on it -- fully exposed. Enable RLS with an "
                        f"anon-deny policy, or REVOKE ALL FROM anon explicitly."
                    )
                continue
            if not _has_anon_deny_policy(cur, table_name):
                anon_has_access = _table_grantees(cur, table_name, "anon")
                if anon_has_access:
                    findings.append(
                        f"Table/view 'public.{table_name}' has RLS enabled but no "
                        f"anon-deny policy (this repo's own '<table>_anon_deny' "
                        f"convention), and anon still holds a grant -- add "
                        f'\'CREATE POLICY "{table_name}_anon_deny" ON public.{table_name} '
                        f"FOR ALL TO anon USING (false);' or an explicit REVOKE."
                    )
    finally:
        conn.close()
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ci", action="store_true", help="Terse output, exit 1 on any finding.")
    args = parser.parse_args()

    findings = run_audit()

    if not findings:
        print(
            "ACL exposure audit: clean. No unprotected table/view/SECURITY DEFINER function found."
        )
        return 0

    if args.ci:
        print(f"ACL exposure audit FAILED: {len(findings)} finding(s).")
        for f in findings:
            print(f"  - {f}")
        return 1

    print(f"ACL exposure audit: {len(findings)} finding(s):\n")
    for f in findings:
        print(f"  - {f}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
