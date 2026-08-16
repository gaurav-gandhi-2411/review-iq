"""Proof that the RLS isolation test suite actually catches a real RLS bypass.

Requires direct DB credentials (postgres superuser, SUPABASE_DB_PASSWORD) in .env.
Marked 'integration' -- skipped in default CI; run explicitly:
    uv run pytest tests/integration/test_rls_disable_proof.py -v -m integration

Why this file exists: a green isolation-test suite is worthless if it would stay green
even when RLS is actually broken. Wave 1 Section E requires proving the suite can catch
the bug, not just asserting it currently doesn't reproduce.

Safety design (read before changing this file): `ALTER TABLE ... DISABLE ROW LEVEL
SECURITY` is a TABLE-WIDE catalog change. Running it against a live, shared production
table with a bare commit would create a real window where EVERY concurrent request
loses tenant isolation -- unacceptable on a database that also serves real traffic.
This file NEVER commits that change. Every test opens one transaction, disables RLS,
runs the assertion that should now show cross-tenant leakage, then unconditionally
rolls back in a `finally` block -- never a `COMMIT`. This is not just "should be fine":
it was empirically verified before this file was written that PostgreSQL's MVCC
catalog-visibility rules mean a concurrent, separate connection querying
`pg_class.relrowsecurity` while this transaction is open and uncommitted sees the
ORIGINAL (enabled) state throughout -- proven via a disposable scratch table, not
assumed. See the Wave 1 Section E report for that proof's exact output.

If a test in this file ever needs to survive across connections (it shouldn't), stop
and reconsider the design rather than switching to a commit.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg2
import pytest
from dotenv import load_dotenv

from tests.integration._superuser_db_params import superuser_db_params

load_dotenv(Path(__file__).parents[2] / ".env")

_DB_PARAMS = superuser_db_params()


def _conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(**_DB_PARAMS)


@pytest.fixture
def two_orgs_one_extraction_each() -> Iterator[tuple[str, str, str, str]]:
    """Fresh org pair + one extraction each, for this proof only. Committed (needs to
    be visible to the disable-RLS transaction as pre-existing data), cleaned up after."""
    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    ext_a, ext_b = str(uuid.uuid4()), str(uuid.uuid4())

    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO public.organizations (id, name, slug) VALUES "
            "(%s, 'RLS Proof A', %s), (%s, 'RLS Proof B', %s)",
            (org_a, f"rlsproof-a-{org_a[:8]}", org_b, f"rlsproof-b-{org_b[:8]}"),
        )
        cur.execute(
            "INSERT INTO public.extractions "
            "(id, org_id, input_hash, extraction, model, prompt_version, schema_version) "
            "VALUES "
            "(%s, %s, 'hash_proof_a', '{}'::jsonb, 'test-model', 'v1.0', 'v1'), "
            "(%s, %s, 'hash_proof_b', '{}'::jsonb, 'test-model', 'v1.0', 'v1')",
            (ext_a, org_a, ext_b, org_b),
        )
        conn.commit()
    finally:
        conn.close()

    yield org_a, org_b, ext_a, ext_b

    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM public.organizations WHERE id IN (%s, %s)", (org_a, org_b))
        conn.commit()
    finally:
        conn.close()


@pytest.mark.integration
class TestRLSDisableProof:
    def test_isolation_test_would_fail_if_rls_were_disabled(
        self, two_orgs_one_extraction_each: tuple[str, str, str, str]
    ) -> None:
        """Re-runs the exact same assertion as
        test_rls_isolation.py::test_org_a_sees_only_own_extraction, but with RLS
        disabled on `extractions` within an uncommitted, always-rolled-back
        transaction. If RLS were doing nothing, this proves the suite would notice --
        the assertion inverts (org A now DOES see org B's row) rather than silently
        staying green."""
        org_a, org_b, ext_a, ext_b = two_orgs_one_extraction_each

        conn = _conn()
        conn.autocommit = False
        try:
            cur = conn.cursor()
            cur.execute("ALTER TABLE public.extractions DISABLE ROW LEVEL SECURITY")

            cur.execute("SET LOCAL ROLE authenticated")
            cur.execute('SET LOCAL "app.current_org_id" = %s', (org_a,))
            cur.execute("SELECT id FROM public.extractions WHERE id IN (%s, %s)", (ext_a, ext_b))
            visible = {str(r[0]) for r in cur.fetchall()}

            # THIS is the proof: with RLS off, org A's session sees org B's row too --
            # the exact failure the normal (RLS-enabled) test suite exists to prevent.
            assert ext_b in visible, (
                "with RLS disabled, org A must be able to see org B's extraction -- "
                "if this assertion fails, RLS enforcement is happening somewhere other "
                "than the policy itself (e.g. an app-level WHERE clause masking the "
                "real test), which would mean the normal isolation tests are not "
                "actually testing what they claim to"
            )
            assert ext_a in visible
        finally:
            # ALWAYS rollback -- never commit. This is what keeps the disabled window
            # invisible to every other concurrent connection (verified via MVCC, see
            # module docstring), and restores RLS the instant this transaction ends.
            conn.rollback()
            conn.close()

        # Post-condition: confirm RLS is really back on before this test returns, from
        # a fresh connection (not the one that ran the ALTER, to prove it's not just
        # this session's local state).
        verify_conn = _conn()
        try:
            vcur = verify_conn.cursor()
            vcur.execute("SELECT relrowsecurity FROM pg_class WHERE relname = 'extractions'")
            (still_enabled,) = vcur.fetchone()
            assert still_enabled is True, (
                "RLS must be enabled on public.extractions after this test -- "
                "the rollback did not restore it, which is a serious problem"
            )
        finally:
            verify_conn.close()

    def test_isolation_test_would_fail_if_alert_log_rls_were_disabled(
        self, two_orgs_one_extraction_each: tuple[str, str, str, str]
    ) -> None:
        """Same proof, against alert_log -- covers the second table family the existing
        suite (TestAlertsRLSIsolation) protects, not just extractions."""
        org_a, org_b, _, _ = two_orgs_one_extraction_each
        log_a = str(uuid.uuid4())

        conn = _conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO public.alert_log (id, org_id, event_type) VALUES (%s, %s, 'high_urgency')",
                (log_a, org_a),
            )
            conn.commit()
        finally:
            conn.close()

        conn = _conn()
        conn.autocommit = False
        try:
            cur = conn.cursor()
            cur.execute("ALTER TABLE public.alert_log DISABLE ROW LEVEL SECURITY")

            cur.execute("SET LOCAL ROLE authenticated")
            cur.execute('SET LOCAL "app.current_org_id" = %s', (org_b,))
            cur.execute("SELECT id FROM public.alert_log WHERE id = %s", (log_a,))
            visible = cur.fetchall()

            assert len(visible) == 1, (
                "with RLS disabled, org B's session must be able to see org A's "
                "alert_log row -- proving the normal (RLS-enabled) denial in "
                "TestAlertsRLSIsolation.test_log_cross_org_select_blocked is real "
                "policy enforcement, not an artifact of some other filter"
            )
        finally:
            conn.rollback()
            conn.close()

        verify_conn = _conn()
        try:
            vcur = verify_conn.cursor()
            vcur.execute("SELECT relrowsecurity FROM pg_class WHERE relname = 'alert_log'")
            (still_enabled,) = vcur.fetchone()
            assert still_enabled is True
        finally:
            verify_conn.close()
