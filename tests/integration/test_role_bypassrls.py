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
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg2
import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env")

from app.core.alerts.storage import list_orgs_with_daily_digest_pg  # noqa: E402
from app.core.storage_pg import list_orgs_with_dated_extractions_pg  # noqa: E402


def _conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(os.environ["SUPABASE_DATABASE_URL"], connect_timeout=15)


def _superuser_conn() -> psycopg2.extensions.connection:
    """Seeding connection for the cross-org fixtures below.

    Deliberately SUPABASE_DIRECT_URL, not tests.integration._superuser_db_params
    (which most other integration files use) -- that helper defaults to the real
    production host whenever TEST_DB_HOST isn't set, and bypassrls-container-check.yml
    (one of the two workflows this file runs under) only ever sets SUPABASE_DIRECT_URL,
    never TEST_DB_HOST/TEST_DB_PORT/SUPABASE_DB_PASSWORD. Using that helper here made
    this file's new tests silently try to reach the real Supabase host from inside a
    GitHub Actions runner and fail with "Network is unreachable" -- caught by this
    workflow's own CI run, not by local testing (which had the fuller env var set
    matching pre-cutover-verification.yml, the other workflow this file runs under).
    SUPABASE_DIRECT_URL is guaranteed set by both workflows and always points at the
    right target in each.
    """
    return psycopg2.connect(os.environ["SUPABASE_DIRECT_URL"], connect_timeout=15)


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


@pytest.fixture
def two_orgs_with_dated_extractions() -> Iterator[tuple[str, str]]:
    """Two orgs, each with one extraction carrying a real review_date.

    Item 224/225: list_orgs_with_dated_extractions_pg previously depended entirely on
    review_iq_app's BYPASSRLS to see rows across orgs (no _set_tenant call) -- proven via
    a throwaway container to silently return 0 rows post-cutover despite rows existing.
    Fixed in 20260817000003_cross_org_sweep_resolvers.sql. This fixture provides the
    cross-org data needed to prove the fix actually works, not just that the function
    doesn't crash.
    """
    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    conn = _superuser_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO public.organizations (id, name, slug) VALUES "
            "(%s, 'Sweep Org A', %s), (%s, 'Sweep Org B', %s)",
            (org_a, f"sweep-a-{org_a[:8]}", org_b, f"sweep-b-{org_b[:8]}"),
        )
        cur.execute(
            "INSERT INTO public.extractions "
            "(org_id, review_date, extraction, input_hash, model, prompt_version, schema_version) "
            "VALUES "
            "(%s, now(), '{}'::jsonb, %s, 'test-model', 'v1', 'v1'), "
            "(%s, now(), '{}'::jsonb, %s, 'test-model', 'v1', 'v1')",
            (org_a, f"hash-a-{org_a[:8]}", org_b, f"hash-b-{org_b[:8]}"),
        )
        conn.commit()
    finally:
        conn.close()

    yield org_a, org_b

    conn = _superuser_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM public.organizations WHERE id IN (%s, %s)", (org_a, org_b))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def two_orgs_with_daily_digest_prefs() -> Iterator[tuple[str, str]]:
    """Two orgs, each with one enabled daily_digest alert_preferences row.

    Same shape as two_orgs_with_dated_extractions above, for
    list_orgs_with_daily_digest_pg's sibling BYPASSRLS-dependency (also fixed in
    20260817000003_cross_org_sweep_resolvers.sql).
    """
    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    conn = _superuser_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO public.organizations (id, name, slug) VALUES "
            "(%s, 'Digest Org A', %s), (%s, 'Digest Org B', %s)",
            (org_a, f"digest-a-{org_a[:8]}", org_b, f"digest-b-{org_b[:8]}"),
        )
        cur.execute(
            "INSERT INTO public.alert_preferences (org_id, event_type, enabled, frequency) "
            "VALUES (%s, 'fake_campaign', true, 'daily_digest'), "
            "(%s, 'batch_defect', true, 'daily_digest')",
            (org_a, org_b),
        )
        conn.commit()
    finally:
        conn.close()

    yield org_a, org_b

    conn = _superuser_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM public.organizations WHERE id IN (%s, %s)", (org_a, org_b))
        conn.commit()
    finally:
        conn.close()


@pytest.mark.integration
class TestCrossOrgSweepFunctionsSeeEveryOrg:
    """Item 224/225: these two functions must see rows across every org despite never
    calling _set_tenant -- proving the SECURITY DEFINER fix actually restores the
    cross-org visibility that used to come from review_iq_app's BYPASSRLS. Would FAIL
    against a pre-#117 checkout (raw query, no BYPASSRLS after cutover -> 0 rows) and
    PASS post-#117 (SECURITY DEFINER function, unaffected by review_iq_app's own
    privileges) -- proven both ways in the PR, not just asserted here."""

    def test_list_orgs_with_dated_extractions_pg_sees_both_orgs(
        self, two_orgs_with_dated_extractions: tuple[str, str]
    ) -> None:
        org_a, org_b = two_orgs_with_dated_extractions
        result = set(list_orgs_with_dated_extractions_pg())
        assert {org_a, org_b} <= result, (
            f"expected both {org_a} and {org_b} in the cross-org sweep result, got {result} -- "
            "if this is empty or missing an org, review_iq_app's BYPASSRLS-dependent path has "
            "regressed (see 20260817000003_cross_org_sweep_resolvers.sql)"
        )

    def test_list_orgs_with_daily_digest_pg_sees_both_orgs(
        self, two_orgs_with_daily_digest_prefs: tuple[str, str]
    ) -> None:
        org_a, org_b = two_orgs_with_daily_digest_prefs
        result = set(list_orgs_with_daily_digest_pg())
        assert {org_a, org_b} <= result, (
            f"expected both {org_a} and {org_b} in the cross-org sweep result, got {result} -- "
            "if this is empty or missing an org, review_iq_app's BYPASSRLS-dependent path has "
            "regressed (see 20260817000003_cross_org_sweep_resolvers.sql)"
        )
