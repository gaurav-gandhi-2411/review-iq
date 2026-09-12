"""Confirms review_iq_app no longer holds BYPASSRLS post-cutover.

RED until the role-separation migration (creates review_iq_migrator, strips
BYPASSRLS from review_iq_app) is applied to production. See
supabase/migrations/20260801000001_role_separation_bypassrls_remediation.sql and
ops/runbooks/bypassrls-remediation-cutover.md.

Connects as review_iq_app itself (SUPABASE_DATABASE_URL) rather than the postgres
superuser DSN the rest of tests/integration/ uses -- pg_roles is world-readable
(rolbypassrls, rolname etc. carry no privilege restriction), so no elevated
credential is needed for this specific check, and review_iq_app's own DSN is the
one actually configured in this repo's CI secrets.

Marked 'integration' — skipped in default CI; run explicitly:
    uv run pytest tests/integration/test_role_bypassrls.py -v -m integration
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg2
import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env")


def _conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(os.environ["SUPABASE_DATABASE_URL"], connect_timeout=15)


def _rolbypassrls(rolname: str) -> bool | None:
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT rolbypassrls FROM pg_roles WHERE rolname = %s", (rolname,))
        row = cur.fetchone()
        return None if row is None else bool(row[0])
    finally:
        conn.close()


@pytest.mark.integration
class TestRoleBypassRLS:
    def test_review_iq_app_does_not_hold_bypassrls(self) -> None:
        """The everyday application role must never hold BYPASSRLS -- that's the S0
        exposure this whole remediation exists to close (Wave 1 / PR #33 origin,
        re-verified live against production 2026-08-01). Migrations and genuinely
        privileged operations use review_iq_migrator instead."""
        bypass = _rolbypassrls("review_iq_app")
        assert bypass is not None, "review_iq_app role must exist"
        assert bypass is False, (
            "review_iq_app holds BYPASSRLS -- every RLS policy on every table is "
            "bypassed for the app's own connection, tenant isolation is not enforced "
            "at the database level at all. See "
            "ops/runbooks/bypassrls-remediation-cutover.md for the fix."
        )

    def test_review_iq_migrator_holds_bypassrls(self) -> None:
        """The privileged migration role must exist and hold BYPASSRLS -- that is its
        entire purpose (schema-scoped, never referenced by application code)."""
        bypass = _rolbypassrls("review_iq_migrator")
        assert bypass is not None, "review_iq_migrator role must exist"
        assert bypass is True, "review_iq_migrator must hold BYPASSRLS to perform migrations"
