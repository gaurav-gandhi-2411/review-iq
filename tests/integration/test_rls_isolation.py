"""Cross-tenant RLS isolation tests against the live Supabase DB.

Requires direct DB credentials (port 5432) in .env.
Marked 'integration' — skipped in default CI; run explicitly:
    uv run pytest tests/integration/test_rls_isolation.py -v -m integration
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg2
import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env")

_DB_PARAMS = {
    "host": "db.enqpluazgxewepchdeut.supabase.co",
    "port": 5432,
    "dbname": "postgres",
    "user": "postgres",
    "password": os.environ["SUPABASE_DB_PASSWORD"],
    "sslmode": "require",
    "connect_timeout": 15,
}


def _conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(**_DB_PARAMS)


@pytest.fixture(scope="module")
def org_ids() -> tuple[str, str]:
    """Create org A and org B; clean up after all tests in the module."""
    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())

    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO public.organizations (id, name, slug) VALUES "
            "(%s, 'Org Alpha', %s), (%s, 'Org Beta', %s)",
            (org_a, f"org-alpha-{org_a[:8]}", org_b, f"org-beta-{org_b[:8]}"),
        )
        conn.commit()
    finally:
        conn.close()

    yield org_a, org_b

    conn = _conn()
    try:
        cur = conn.cursor()
        # CASCADE deletes extractions, api_keys, usage_records, members
        cur.execute(
            "DELETE FROM public.organizations WHERE id IN (%s, %s)",
            (org_a, org_b),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(scope="module")
def extraction_ids(org_ids: tuple[str, str]) -> tuple[str, str]:
    """Insert one extraction per org; IDs returned for assertion."""
    org_a, org_b = org_ids
    ext_a, ext_b = str(uuid.uuid4()), str(uuid.uuid4())

    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO public.extractions "
            "(id, org_id, input_hash, extraction, model, prompt_version, schema_version) "
            "VALUES "
            "(%s, %s, 'hash_rls_a', '{\"stars\": 4}'::jsonb, 'test-model', 'v1.0', 'v1'), "
            "(%s, %s, 'hash_rls_b', '{\"stars\": 2}'::jsonb, 'test-model', 'v1.0', 'v1')",
            (ext_a, org_a, ext_b, org_b),
        )
        conn.commit()
    finally:
        conn.close()

    return ext_a, ext_b


def _as_authenticated(org_id: str) -> psycopg2.extensions.connection:
    """Return an open connection mid-transaction scoped to authenticated + org."""
    conn = _conn()
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute("SET LOCAL ROLE authenticated")
    cur.execute('SET LOCAL "app.current_org_id" = %s', (org_id,))
    return conn


@pytest.mark.integration
class TestRLSIsolation:
    def test_org_a_sees_only_own_extraction(
        self, extraction_ids: tuple[str, str], org_ids: tuple[str, str]
    ) -> None:
        org_a, org_b = org_ids
        ext_a, ext_b = extraction_ids

        conn = _as_authenticated(org_a)
        try:
            cur = conn.cursor()
            cur.execute("SELECT id FROM public.extractions")
            visible = {str(r[0]) for r in cur.fetchall()}
        finally:
            conn.rollback()
            conn.close()

        assert ext_a in visible, "Org A must see its own extraction"
        assert ext_b not in visible, "Org A must NOT see org B extraction"

    def test_org_b_sees_only_own_extraction(
        self, extraction_ids: tuple[str, str], org_ids: tuple[str, str]
    ) -> None:
        org_a, org_b = org_ids
        ext_a, ext_b = extraction_ids

        conn = _as_authenticated(org_b)
        try:
            cur = conn.cursor()
            cur.execute("SELECT id FROM public.extractions")
            visible = {str(r[0]) for r in cur.fetchall()}
        finally:
            conn.rollback()
            conn.close()

        assert ext_b in visible, "Org B must see its own extraction"
        assert ext_a not in visible, "Org B must NOT see org A extraction"

    def test_org_a_cannot_update_org_b_extraction(
        self, extraction_ids: tuple[str, str], org_ids: tuple[str, str]
    ) -> None:
        org_a, org_b = org_ids
        _, ext_b = extraction_ids

        conn = _as_authenticated(org_a)
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE public.extractions SET model = 'hacked' WHERE id = %s",
                (ext_b,),
            )
            assert cur.rowcount == 0, "UPDATE of cross-tenant row must affect 0 rows"
        finally:
            conn.rollback()
            conn.close()

    def test_org_a_cannot_delete_org_b_extraction(
        self, extraction_ids: tuple[str, str], org_ids: tuple[str, str]
    ) -> None:
        org_a, org_b = org_ids
        _, ext_b = extraction_ids

        conn = _as_authenticated(org_a)
        try:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM public.extractions WHERE id = %s",
                (ext_b,),
            )
            assert cur.rowcount == 0, "DELETE of cross-tenant row must affect 0 rows"
        finally:
            conn.rollback()
            conn.close()

    def test_no_org_context_sees_nothing(self) -> None:
        """authenticated role with no org context returns zero rows."""
        conn = _conn()
        conn.autocommit = False
        try:
            cur = conn.cursor()
            cur.execute("SET LOCAL ROLE authenticated")
            # Intentionally do NOT set app.current_org_id → current_org_id() returns NULL
            cur.execute("SELECT id FROM public.extractions")
            rows = cur.fetchall()
        finally:
            conn.rollback()
            conn.close()

        assert rows == [], "NULL org context must return no rows (RLS NULL guard)"

    def test_org_a_cannot_insert_into_org_b(self, org_ids: tuple[str, str]) -> None:
        """WITH CHECK clause must prevent INSERT with foreign org_id."""
        org_a, org_b = org_ids
        ghost_ext = str(uuid.uuid4())

        conn = _as_authenticated(org_a)
        try:
            cur = conn.cursor()
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute(
                    "INSERT INTO public.extractions "
                    "(id, org_id, input_hash, extraction, model, "
                    " prompt_version, schema_version) "
                    "VALUES (%s, %s, 'hash_check_b', '{}'::jsonb, "
                    " 'test-model', 'v1.0', 'v1')",
                    (ghost_ext, org_b),
                )
        finally:
            conn.rollback()
            conn.close()

    def test_nonexistent_org_id_sees_nothing(self) -> None:
        """A valid UUID that doesn't map to any org must return zero rows, not an error."""
        ghost = str(uuid.uuid4())  # valid UUID format, but no org with this id exists

        conn = _as_authenticated(ghost)
        try:
            cur = conn.cursor()
            cur.execute("SELECT id FROM public.extractions")
            rows = cur.fetchall()
        finally:
            conn.rollback()
            conn.close()

        assert rows == [], "Non-existent org UUID must silently return no rows"


@pytest.mark.integration
class TestAlertsRLSIsolation:
    """Proves alert_preferences and alert_log are tenant-isolated.

    Covers:
      - Cross-org SELECT blocked (org B sees zero rows from org A)
      - Cross-org INSERT blocked by WITH CHECK (not just USING)
      - anon denied on both tables
      - alert_log UPDATE blocked at the grant layer (append-only, no UPDATE grant)
    """

    # ------------------------------------------------------------------
    # alert_preferences
    # ------------------------------------------------------------------

    def test_prefs_matching_org_insert_succeeds(self, org_ids: tuple[str, str]) -> None:
        """Sanity: authenticated org can INSERT its own preferences row."""
        org_a, _ = org_ids
        conn = _as_authenticated(org_a)
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO public.alert_preferences (org_id, event_type, enabled, frequency) "
                "VALUES (%s, 'high_urgency', true, 'immediate')",
                (org_a,),
            )
            assert cur.rowcount == 1, "Own-org INSERT must succeed"
        finally:
            conn.rollback()  # keep DB clean; isolation test, not a data test
            conn.close()

    def test_prefs_cross_org_select_blocked(self, org_ids: tuple[str, str]) -> None:
        """Org B must see zero rows from org A's alert_preferences."""
        org_a, org_b = org_ids

        # Seed a committed row as service_role (bypasses RLS); CASCADE delete cleans up.
        conn = _conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO public.alert_preferences (org_id, event_type) "
                "VALUES (%s, 'topic_spike')",
                (org_a,),
            )
            conn.commit()
        finally:
            conn.close()

        conn = _as_authenticated(org_b)
        try:
            cur = conn.cursor()
            cur.execute("SELECT id FROM public.alert_preferences")
            rows = cur.fetchall()
        finally:
            conn.rollback()
            conn.close()

        assert rows == [], "Org B must see zero rows from org A's alert_preferences"

    def test_prefs_with_check_blocks_cross_org_insert(self, org_ids: tuple[str, str]) -> None:
        """WITH CHECK must block org A inserting a row tagged with org B's org_id.

        Expected: psycopg2.errors.InsufficientPrivilege (SQLSTATE 42501) with a message
        that confirms the RLS WITH CHECK path ("row-level security policy"), not a generic
        privilege denial. We assert the message text explicitly so this test cannot pass
        on a different error path (e.g. a missing grant instead of a policy block).
        """
        org_a, org_b = org_ids
        conn = _as_authenticated(org_a)
        try:
            cur = conn.cursor()
            with pytest.raises(psycopg2.errors.InsufficientPrivilege) as exc_info:
                cur.execute(
                    "INSERT INTO public.alert_preferences (org_id, event_type) "
                    "VALUES (%s, 'likely_fake')",
                    (org_b,),  # org_a's session, org_b's id — must be blocked by WITH CHECK
                )
        finally:
            conn.rollback()
            conn.close()

        assert "row-level security policy" in str(exc_info.value), (
            f"Exception must be a WITH CHECK RLS violation, got: {exc_info.value}"
        )

    def test_prefs_anon_select_denied(self) -> None:
        """anon role must be denied SELECT on alert_preferences."""
        conn = _conn()
        conn.autocommit = False
        try:
            cur = conn.cursor()
            cur.execute("SET LOCAL ROLE anon")
            cur.execute("SELECT id FROM public.alert_preferences")
            rows = cur.fetchall()
        finally:
            conn.rollback()
            conn.close()
        assert rows == [], "anon must see no rows (denied by alert_prefs_anon_deny policy)"

    # ------------------------------------------------------------------
    # alert_log
    # ------------------------------------------------------------------

    def test_log_matching_org_insert_succeeds(self, org_ids: tuple[str, str]) -> None:
        """Sanity: authenticated org can INSERT its own alert_log row."""
        org_a, _ = org_ids
        conn = _as_authenticated(org_a)
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO public.alert_log (org_id, event_type) VALUES (%s, 'high_urgency')",
                (org_a,),
            )
            assert cur.rowcount == 1, "Own-org INSERT into alert_log must succeed"
        finally:
            conn.rollback()
            conn.close()

    def test_log_cross_org_select_blocked(self, org_ids: tuple[str, str]) -> None:
        """Org B must see zero rows from org A's alert_log."""
        org_a, org_b = org_ids

        conn = _conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO public.alert_log (org_id, event_type) VALUES (%s, 'fake_cluster')",
                (org_a,),
            )
            conn.commit()
        finally:
            conn.close()

        conn = _as_authenticated(org_b)
        try:
            cur = conn.cursor()
            cur.execute("SELECT id FROM public.alert_log")
            rows = cur.fetchall()
        finally:
            conn.rollback()
            conn.close()

        assert rows == [], "Org B must see zero rows from org A's alert_log"

    def test_log_with_check_blocks_cross_org_insert(self, org_ids: tuple[str, str]) -> None:
        """WITH CHECK must block org A inserting an alert_log row tagged with org B's org_id.

        Same exception-class and message-text verification as the prefs equivalent.
        """
        org_a, org_b = org_ids
        conn = _as_authenticated(org_a)
        try:
            cur = conn.cursor()
            with pytest.raises(psycopg2.errors.InsufficientPrivilege) as exc_info:
                cur.execute(
                    "INSERT INTO public.alert_log (org_id, event_type) VALUES (%s, 'likely_fake')",
                    (org_b,),
                )
        finally:
            conn.rollback()
            conn.close()

        assert "row-level security policy" in str(exc_info.value), (
            f"Exception must be a WITH CHECK RLS violation, got: {exc_info.value}"
        )

    def test_log_update_blocked_by_rls(self, org_ids: tuple[str, str]) -> None:
        """alert_log is append-only: UPDATE must be silently denied even for the owning org.

        Supabase pre-grants ALL privileges to authenticated via DEFAULT PRIVILEGES, so
        the denial cannot come from the grant layer. It comes instead from the absence
        of an UPDATE RLS policy: no matching policy → PostgreSQL default-deny → 0 rows
        affected (no error). This is the same mechanism as the existing
        test_org_a_cannot_update_org_b_extraction test on extractions.
        """
        org_a, _ = org_ids

        # Seed a committed row as service_role so there is something to try to update.
        log_id = str(uuid.uuid4())
        conn = _conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO public.alert_log (id, org_id, event_type) "
                "VALUES (%s, %s, 'high_urgency')",
                (log_id, org_a),
            )
            conn.commit()
        finally:
            conn.close()

        conn = _as_authenticated(org_a)
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE public.alert_log SET event_type = 'tampered' WHERE id = %s",
                (log_id,),
            )
            assert cur.rowcount == 0, (
                "UPDATE on append-only alert_log must affect 0 rows (no UPDATE RLS policy)"
            )
        finally:
            conn.rollback()
            conn.close()

    def test_log_anon_select_denied(self) -> None:
        """anon role must be denied SELECT on alert_log."""
        conn = _conn()
        conn.autocommit = False
        try:
            cur = conn.cursor()
            cur.execute("SET LOCAL ROLE anon")
            cur.execute("SELECT id FROM public.alert_log")
            rows = cur.fetchall()
        finally:
            conn.rollback()
            conn.close()
        assert rows == [], "anon must see no rows (denied by alert_log_anon_deny policy)"
