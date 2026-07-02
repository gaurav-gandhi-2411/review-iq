"""RLS isolation proof for google_business_installations table.

Run AFTER applying 20260702000001_google_business_installations.sql.
Requires direct DB credentials (port 5432) in .env.

    uv run pytest tests/integration/test_google_business_installations_rls.py -v -m integration

WRITE PATH (OAuth callback):
    google_business_installations rows are written via service-role (postgres, no
    SET ROLE), exactly like shopify_installations. org_id is resolved server-side
    from the authenticated seller's Supabase JWT:

        user  = verify_supabase_jwt(bearer_token)      # Supabase Admin SDK
        org   = _get_org_for_user(str(user.id))        # postgres-role lookup
        INSERT INTO google_business_installations (org_id=org["org_id"], ...)

    org_id is NEVER accepted as a user-controlled parameter.

INSERT BLOCK MECHANISM:
    Supabase pre-grants all privileges (authenticated=arwdDxtm) via DEFAULT PRIVILEGES,
    so explicit GRANT lines are documentation-only. The INSERT block is RLS-based:
    no INSERT policy exists for the authenticated role → PostgreSQL default-deny.
    The raised exception is psycopg2.errors.InsufficientPrivilege (SQLSTATE 42501)
    with "row-level security policy" in the message — NOT a grant-layer error.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg2
import psycopg2.errors
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

_FAKE_ENC_REFRESH_TOKEN = "gAAAAABfake_fernet_ciphertext_for_rls_test_only"


def _conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(**_DB_PARAMS)


def _set_tenant(cur: object, org_id: str) -> None:
    cur.execute("SET LOCAL ROLE authenticated")  # type: ignore[union-attr]
    cur.execute("SET LOCAL app.current_org_id TO %s", (org_id,))  # type: ignore[union-attr]


@pytest.fixture(scope="module")
def org_ids() -> tuple[str, str]:
    """Create org A and org B; CASCADE teardown removes all child rows after tests."""
    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())

    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO public.organizations (id, name, slug) VALUES "
            "(%s, 'GBP RLS Org A', %s), (%s, 'GBP RLS Org B', %s)",
            (org_a, f"gbp-rls-a-{org_a[:8]}", org_b, f"gbp-rls-b-{org_b[:8]}"),
        )
        conn.commit()
    finally:
        conn.close()

    yield org_a, org_b

    conn = _conn()
    try:
        cur = conn.cursor()
        # CASCADE deletes installations, extractions, api_keys, members
        cur.execute(
            "DELETE FROM public.organizations WHERE id IN (%s, %s)",
            (org_a, org_b),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(scope="module")
def installation_ids(org_ids: tuple[str, str]) -> tuple[str, str]:
    """Insert one active installation per org via service-role (postgres).

    Mirrors the OAuth callback write path: postgres role, org_id explicitly set
    from the authenticated seller's resolved session — not from user input.
    """
    org_a, org_b = org_ids
    location_a = f"accounts/rls-a-{org_a[:8]}/locations/loc-a-{org_a[:8]}"
    location_b = f"accounts/rls-b-{org_b[:8]}/locations/loc-b-{org_b[:8]}"

    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO public.google_business_installations "
            "(org_id, google_account_name, google_location_name, refresh_token_enc) "
            "VALUES (%s, %s, %s, %s), (%s, %s, %s, %s) RETURNING id",
            (
                org_a,
                f"accounts/rls-a-{org_a[:8]}",
                location_a,
                _FAKE_ENC_REFRESH_TOKEN,
                org_b,
                f"accounts/rls-b-{org_b[:8]}",
                location_b,
                _FAKE_ENC_REFRESH_TOKEN,
            ),
        )
        rows = cur.fetchall()
        conn.commit()
        return str(rows[0][0]), str(rows[1][0])
    finally:
        conn.close()


@pytest.mark.integration
class TestGoogleBusinessInstallationsRLS:
    """Multi-tenant isolation proof for google_business_installations + webhook write scope."""

    # ------------------------------------------------------------------
    # SELECT isolation
    # ------------------------------------------------------------------

    def test_org_a_sees_only_own_installation(
        self, org_ids: tuple[str, str], installation_ids: tuple[str, str]
    ) -> None:
        """SET ROLE authenticated + org_a context → returns only org_a's row."""
        org_a, _ = org_ids
        inst_a, inst_b = installation_ids

        conn = _conn()
        try:
            cur = conn.cursor()
            _set_tenant(cur, org_a)
            cur.execute("SELECT id FROM public.google_business_installations")
            visible_ids = {str(row[0]) for row in cur.fetchall()}
        finally:
            conn.close()

        assert inst_a in visible_ids, "org_a must see its own installation"
        assert inst_b not in visible_ids, "org_a must NOT see org_b's installation"

    def test_org_b_sees_only_own_installation(
        self, org_ids: tuple[str, str], installation_ids: tuple[str, str]
    ) -> None:
        """SET ROLE authenticated + org_b context → returns only org_b's row."""
        _, org_b = org_ids
        inst_a, inst_b = installation_ids

        conn = _conn()
        try:
            cur = conn.cursor()
            _set_tenant(cur, org_b)
            cur.execute("SELECT id FROM public.google_business_installations")
            visible_ids = {str(row[0]) for row in cur.fetchall()}
        finally:
            conn.close()

        assert inst_b in visible_ids, "org_b must see its own installation"
        assert inst_a not in visible_ids, "org_b must NOT see org_a's installation"

    def test_anon_cannot_select(self, installation_ids: tuple[str, str]) -> None:
        """anon role denied by policy — SELECT returns 0 rows."""
        conn = _conn()
        try:
            cur = conn.cursor()
            cur.execute("SET LOCAL ROLE anon")
            cur.execute("SELECT id FROM public.google_business_installations")
            assert cur.fetchall() == [], "anon must see no installations"
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # INSERT block — mechanism is RLS (no INSERT policy), NOT grant layer
    # ------------------------------------------------------------------

    def test_authenticated_same_org_insert_blocked_by_rls(
        self, org_ids: tuple[str, str]
    ) -> None:
        """authenticated INSERT is blocked by RLS even for same-org rows.

        The OAuth callback writes as service-role (postgres, no SET ROLE). Direct
        authenticated INSERT is structurally blocked regardless of the org_id value.
        """
        org_a, _ = org_ids

        conn = _conn()
        try:
            cur = conn.cursor()
            _set_tenant(cur, org_a)
            with pytest.raises(psycopg2.errors.InsufficientPrivilege) as exc_info:
                cur.execute(
                    "INSERT INTO public.google_business_installations "
                    "(org_id, google_account_name, google_location_name, refresh_token_enc) "
                    "VALUES (%s, %s, %s, %s)",
                    (org_a, "accounts/blocked", "accounts/blocked/locations/same-org", _FAKE_ENC_REFRESH_TOKEN),
                )
            assert "row-level security policy" in str(exc_info.value), (
                "Block must come from RLS (no INSERT policy), not the grant layer"
            )
            conn.rollback()
        finally:
            conn.close()

    def test_authenticated_cross_org_insert_blocked_by_rls(
        self, org_ids: tuple[str, str]
    ) -> None:
        """Cross-org INSERT (org A session, org B's org_id) is also blocked by RLS.

        Both attacks fail structurally — the block is not contingent on the org_id
        value, so there is no way to enumerate org_ids to find an exploitable path.
        """
        org_a, org_b = org_ids

        conn = _conn()
        try:
            cur = conn.cursor()
            _set_tenant(cur, org_a)       # authenticated as org A
            with pytest.raises(psycopg2.errors.InsufficientPrivilege) as exc_info:
                cur.execute(
                    "INSERT INTO public.google_business_installations "
                    "(org_id, google_account_name, google_location_name, refresh_token_enc) "
                    "VALUES (%s, %s, %s, %s)",
                    (org_b, "accounts/attack", "accounts/attack/locations/cross-org", _FAKE_ENC_REFRESH_TOKEN),
                )
            assert "row-level security policy" in str(exc_info.value), (
                "Cross-org block must also be RLS, not grant-layer"
            )
            conn.rollback()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Service-role bypass + UNIQUE constraint
    # ------------------------------------------------------------------

    def test_service_role_webhook_lookup_bypasses_rls(
        self, org_ids: tuple[str, str], installation_ids: tuple[str, str]
    ) -> None:
        """Postgres role (no SET ROLE) sees ALL rows — mirrors _get_google_installation_pg."""
        org_a, org_b = org_ids
        inst_a, inst_b = installation_ids

        conn = _conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id FROM public.google_business_installations WHERE id IN (%s, %s)",
                (inst_a, inst_b),
            )
            visible_ids = {str(row[0]) for row in cur.fetchall()}
        finally:
            conn.close()

        assert inst_a in visible_ids and inst_b in visible_ids, (
            "service-role must see both installs for webhook routing"
        )

    def test_location_name_unique_prevents_duplicate_locations(
        self, org_ids: tuple[str, str]
    ) -> None:
        """A second service-role INSERT with the same google_location_name must raise UniqueViolation.

        UNIQUE(google_location_name) is the anti-ambiguity gate that makes webhook
        routing unambiguous: at most one org_id per location, enforced at the DB level.
        """
        org_a, org_b = org_ids
        location_a = f"accounts/rls-a-{org_a[:8]}/locations/loc-a-{org_a[:8]}"

        conn = _conn()
        try:
            cur = conn.cursor()
            with pytest.raises(psycopg2.errors.UniqueViolation):
                # Service-role INSERT attempting to steal location_a for org_b
                cur.execute(
                    "INSERT INTO public.google_business_installations "
                    "(org_id, google_account_name, google_location_name, refresh_token_enc) "
                    "VALUES (%s, %s, %s, %s)",
                    (org_b, f"accounts/rls-b-{org_b[:8]}", location_a, _FAKE_ENC_REFRESH_TOKEN),
                )
            conn.rollback()
        finally:
            conn.close()

    def test_unrecognized_location_name_returns_no_row(self) -> None:
        """Webhook from a location that was never installed returns 0 rows — no fallback org."""
        conn = _conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT org_id FROM public.google_business_installations "
                "WHERE google_location_name = %s AND revoked_at IS NULL",
                ("accounts/never-installed/locations/never-installed",),
            )
            assert cur.fetchone() is None, "unrecognized location must return no row"
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # End-to-end webhook write-scope proof
    # ------------------------------------------------------------------

    def test_webhook_lookup_and_extraction_scope(
        self, org_ids: tuple[str, str], installation_ids: tuple[str, str]
    ) -> None:
        """Webhook routing chain: location_a → org_a → extraction visible only to org A.

        Proves the full path a Pub/Sub NEW_REVIEW push handler would walk:
          1. location_name (from the notification) → google_business_installations
             lookup → org_a_id
          2. Extraction written under org_a_id (service-role, same as app code)
          3. org A authenticated sees the extraction; org B does not

        This is the same cross-tenant risk shopify's equivalent test proves against:
        a webhook from location A writing into location B's org. This test proves
        that can't happen because the routing resolves org_a_id from the location
        name, and the extraction is then RLS-scoped to that org_id.
        """
        org_a, org_b = org_ids
        location_a = f"accounts/rls-a-{org_a[:8]}/locations/loc-a-{org_a[:8]}"
        extraction_hash = f"gbp-scope-proof-{org_a[:8]}"

        # Step 1: simulate _get_google_installation_pg(location_a) — postgres-role lookup.
        conn = _conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT org_id FROM public.google_business_installations "
                "WHERE google_location_name = %s AND revoked_at IS NULL",
                (location_a,),
            )
            row = cur.fetchone()
            assert row is not None, f"location_a ({location_a}) must resolve to an installation"
            resolved_org_id = str(row[0])
            assert resolved_org_id == org_a, (
                f"location_a must resolve to org_a ({org_a}), got {resolved_org_id}"
            )
        finally:
            conn.close()

        # Step 2: write extraction under resolved org_id — service-role INSERT,
        # mirrors save_extraction_pg(org_id=resolved_org_id, api_key_id=None, ...).
        conn = _conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO public.extractions "
                "(id, org_id, input_hash, extraction, model, prompt_version, schema_version) "
                "VALUES (%s, %s, %s, '{\"stars\": 5}'::jsonb, 'test-model', 'v1.0', '1.0.0')",
                (str(uuid.uuid4()), resolved_org_id, extraction_hash),
            )
            conn.commit()
        finally:
            conn.close()

        # Step 3a: org A authenticated can see their GBP-ingested extraction.
        conn = _conn()
        try:
            cur = conn.cursor()
            _set_tenant(cur, org_a)
            cur.execute(
                "SELECT COUNT(*) FROM public.extractions WHERE input_hash = %s",
                (extraction_hash,),
            )
            assert cur.fetchone()[0] == 1, (
                "org A must see the extraction ingested via their GBP location"
            )
        finally:
            conn.close()

        # Step 3b: org B cannot see org A's GBP-ingested extraction.
        conn = _conn()
        try:
            cur = conn.cursor()
            _set_tenant(cur, org_b)
            cur.execute(
                "SELECT COUNT(*) FROM public.extractions WHERE input_hash = %s",
                (extraction_hash,),
            )
            assert cur.fetchone()[0] == 0, (
                "org B must NOT see org A's GBP-ingested extraction"
            )
        finally:
            conn.close()

        # Extraction cleanup: org teardown in org_ids fixture cascades this,
        # but explicit delete avoids hash collisions if the test module is re-run.
        conn = _conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM public.extractions WHERE input_hash = %s", (extraction_hash,)
            )
            conn.commit()
        finally:
            conn.close()
