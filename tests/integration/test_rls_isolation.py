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


def _as_raw_review_iq_app(org_id: str) -> psycopg2.extensions.connection:
    """Connect using review_iq_app's OWN production credential (SUPABASE_DATABASE_URL)
    with NO SET ROLE at all -- current_user stays review_iq_app for the whole session.

    Deliberately does NOT replicate app/core/storage_pg.py's _set_tenant(), which
    calls `SET LOCAL ROLE authenticated` before setting the org GUC. That SET ROLE
    switches current_user away from review_iq_app to authenticated, which does NOT
    hold BYPASSRLS -- so a _set_tenant()-covered call site is already safe today,
    BYPASSRLS or not, and testing that path here would not be RED pre-cutover
    (confirmed empirically before writing this test).

    This connects exactly as the actually-exposed call sites do instead: admin.py's
    helpers (which never call _set_tenant() by design -- they need cross-org access)
    and any future call site that forgets to call it. Pre-cutover, review_iq_app
    holds BYPASSRLS directly, so a raw connection like this sees every row regardless
    of app.current_org_id -- that's the real S0 exposure. Post-cutover, this is the
    proof ADDITION 1 asked for: does RLS actually cover a connection using review_iq_app's
    identity with no explicit role switch, via its inherited membership in authenticated
    (policies are scoped `TO authenticated`, not `TO review_iq_app` -- the verifier's
    exact concern) -- or does removing BYPASSRLS alone leave this path either still
    leaking cross-tenant data, or blocked from ALL data including its own.
    """
    conn = psycopg2.connect(os.environ["SUPABASE_DATABASE_URL"], connect_timeout=15)
    conn.autocommit = False
    cur = conn.cursor()
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


@pytest.mark.integration
class TestReviewIqAppCredentialIsolation:
    """BYPASSRLS remediation ADDITION 1: prove a cross-tenant read is blocked at the
    database level using review_iq_app's own raw credential -- no SET ROLE, the
    actual production DSN (SUPABASE_DATABASE_URL), current_user left as review_iq_app
    for the whole session. See _as_raw_review_iq_app()'s docstring for why this (and
    not a _set_tenant()-style SET ROLE authenticated connection, which is already
    safe today regardless of BYPASSRLS) is the test that actually represents the S0
    exposure: admin.py's helpers, and any future call site that forgets to call
    _set_tenant(), connect exactly like this.

    RED until the BYPASSRLS-removal cutover (Task 2 / role-separation migration) is
    applied: pre-cutover, review_iq_app holds BYPASSRLS directly (no SET ROLE to lose
    it), so RLS is bypassed entirely and every row is visible to every org.

    The own-org test is not a formality -- the verifier flagged that RLS policies are
    scoped to `TO authenticated`/`TO anon` only, so removing BYPASSRLS alone could
    leave a raw review_iq_app connection with *no* access at all (if inherited role
    membership in `authenticated` turns out not to be enough for policy matching
    without an explicit SET ROLE) rather than correctly-scoped access. Both directions
    must be tested, not just the cross-tenant block -- if only the cross-org test goes
    green post-cutover while this one goes red, that's Task C3's signal that
    additional RLS policy work (not just removing BYPASSRLS) is required.
    """

    def test_review_iq_app_own_org_read_succeeds(
        self, extraction_ids: tuple[str, str], org_ids: tuple[str, str]
    ) -> None:
        org_a, _ = org_ids
        ext_a, _ = extraction_ids

        conn = _as_raw_review_iq_app(org_a)
        try:
            cur = conn.cursor()
            cur.execute("SELECT id FROM public.extractions")
            visible = {str(r[0]) for r in cur.fetchall()}
        finally:
            conn.rollback()
            conn.close()

        assert ext_a in visible, (
            "review_iq_app's raw credential must still see its own org's data "
            "post-cutover -- if this fails while the cross-org test below passes, the "
            "app lost access entirely rather than being correctly scoped (exactly the "
            "failure mode ADDITION 1 warned about: policies scoped to authenticated/"
            "anon only, not verified to actually cover a raw review_iq_app session)."
        )

    def test_review_iq_app_cross_org_read_blocked(
        self, extraction_ids: tuple[str, str], org_ids: tuple[str, str]
    ) -> None:
        org_a, _ = org_ids
        _, ext_b = extraction_ids

        conn = _as_raw_review_iq_app(org_a)
        try:
            cur = conn.cursor()
            cur.execute("SELECT id FROM public.extractions")
            visible = {str(r[0]) for r in cur.fetchall()}
        finally:
            conn.rollback()
            conn.close()

        assert ext_b not in visible, (
            "review_iq_app's raw credential must NOT see another org's data. This is "
            "the database-level proof (not application WHERE-clause logic, and not "
            "reliant on every call site remembering to call _set_tenant()) that "
            "BYPASSRLS removal actually closes the exposure -- fails today because "
            "review_iq_app still holds BYPASSRLS pre-cutover with no SET ROLE to lose it."
        )
