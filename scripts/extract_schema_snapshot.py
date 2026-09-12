"""Extract a normalized schema snapshot from a Postgres database for drift comparison.

P0, 2026-08-01: the ephemeral pre-cutover verification job (pre-cutover-verification.yml)
is only as trustworthy as the migration set is complete -- quota_requests existed in
production with no CREATE TABLE anywhere in supabase/migrations/, and nothing caught it
until someone happened to try applying every migration to a clean database. This script
is the general tool for that check: it captures tables/columns/indexes/constraints/RLS
policies/functions/grants/defaults from a live connection, in a normalized, comparable
form (no volatile bits: OIDs, exact index names Postgres autogenerates differently across
databases, timestamps).

Usage:
    uv run python scripts/extract_schema_snapshot.py "$DSN" > snapshot.json

Compared by scripts/check_schema_drift.py, which is the actual CI gate.
"""

from __future__ import annotations

import json
import sys

import psycopg2


def _rows(cur: object, sql: str) -> list[dict[str, object]]:
    cur.execute(sql)  # type: ignore[attr-defined]
    cols = [d.name for d in cur.description]  # type: ignore[attr-defined]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]  # type: ignore[attr-defined]


def extract_snapshot(dsn: str) -> dict[str, object]:
    conn = psycopg2.connect(dsn, connect_timeout=15)
    try:
        cur = conn.cursor()

        columns = _rows(
            cur,
            """
            SELECT table_name, column_name, data_type, is_nullable, column_default,
                   character_maximum_length, numeric_precision, numeric_scale
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position
            """,
        )

        constraints = _rows(
            cur,
            """
            SELECT conrelid::regclass::text AS table_name, conname,
                   pg_get_constraintdef(oid) AS definition
            FROM pg_constraint
            WHERE connamespace = 'public'::regnamespace
            ORDER BY conrelid::regclass::text, conname
            """,
        )

        indexes = _rows(
            cur,
            """
            SELECT tablename AS table_name, indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public'
            -- Exclude indexes backing a constraint (already captured above,
            -- and Postgres sometimes names these differently across databases
            -- depending on creation order) -- keep only genuinely standalone indexes.
            AND indexname NOT IN (
                SELECT conname FROM pg_constraint WHERE connamespace = 'public'::regnamespace
            )
            ORDER BY tablename, indexname
            """,
        )

        rls_enabled = _rows(
            cur,
            """
            SELECT c.relname AS table_name, c.relrowsecurity AS rls_enabled,
                   c.relforcerowsecurity AS rls_forced
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r'
            ORDER BY c.relname
            """,
        )

        policies = _rows(
            cur,
            """
            SELECT tablename AS table_name, policyname, cmd, roles::text AS roles,
                   qual AS using_expr, with_check AS with_check_expr
            FROM pg_policies
            WHERE schemaname = 'public'
            ORDER BY tablename, policyname
            """,
        )

        functions = _rows(
            cur,
            """
            SELECT p.proname AS function_name,
                   pg_get_function_identity_arguments(p.oid) AS arguments,
                   pg_get_function_result(p.oid) AS return_type,
                   p.prosecdef AS security_definer,
                   l.lanname AS language
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            JOIN pg_language l ON l.oid = p.prolang
            WHERE n.nspname = 'public'
            ORDER BY p.proname, arguments
            """,
        )

        function_grants = _rows(
            cur,
            """
            SELECT r.routine_name AS function_name, g.grantee, g.privilege_type
            FROM information_schema.routine_privileges g
            JOIN information_schema.routines r ON r.specific_name = g.specific_name
            WHERE r.specific_schema = 'public'
            ORDER BY r.routine_name, g.grantee, g.privilege_type
            """,
        )

        table_grants = _rows(
            cur,
            """
            SELECT table_name, grantee, privilege_type
            FROM information_schema.role_table_grants
            WHERE table_schema = 'public'
            ORDER BY table_name, grantee, privilege_type
            """,
        )

        roles = _rows(
            cur,
            """
            SELECT rolname, rolbypassrls, rolcanlogin
            FROM pg_roles
            WHERE rolname IN (
                'review_iq_app', 'review_iq_admin', 'review_iq_migrator',
                'anon', 'authenticated', 'service_role'
            )
            ORDER BY rolname
            """,
        )

        return {
            "columns": columns,
            "constraints": constraints,
            "indexes": indexes,
            "rls_enabled": rls_enabled,
            "policies": policies,
            "functions": functions,
            "function_grants": function_grants,
            "table_grants": table_grants,
            "roles": roles,
        }
    finally:
        conn.close()


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: extract_schema_snapshot.py <DSN>", file=sys.stderr)
        return 2
    snapshot = extract_snapshot(sys.argv[1])
    json.dump(snapshot, sys.stdout, indent=2, default=str, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
