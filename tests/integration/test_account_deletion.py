"""Integration proof for DELETE /account (audit finding #7, self-service org deletion).

Against the LIVE Supabase DB (no mocks on the delete path itself):
  1. Deleting org A removes org A's rows from every table seeded with data
     (organizations, organization_members, api_keys, extractions).
  2. Org B's data in every one of those same tables is completely untouched.
  3. A slug mismatch deletes nothing, in either org.

Requires direct DB credentials (SUPABASE_DB_PASSWORD) in .env, same connection
pattern as tests/integration/test_rls_isolation.py.

Marked 'integration' — skipped by default; run explicitly:
    uv run pytest tests/integration/test_account_deletion.py -v -m integration
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import psycopg2
import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env")

from app.api.account import _do_delete_org  # noqa: E402
from app.core.schemas import ExtractionMetaV2, ReviewExtractionV2, Sentiment, Urgency  # noqa: E402
from app.core.storage_pg import get_by_hash_pg, save_extraction_pg  # noqa: E402
from fastapi import HTTPException  # noqa: E402

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


def _make_extraction(org_id: str, input_hash: str) -> ReviewExtractionV2:
    return ReviewExtractionV2(
        product="Deletion Test Widget",
        sentiment=Sentiment.positive,
        urgency=Urgency.low,
        extraction_meta=ExtractionMetaV2(
            model="mock",
            prompt_version="v2.0",
            schema_version="1.0.0",
            extracted_at=datetime.now(tz=UTC),
            input_hash=input_hash,
            org_id=org_id,
        ),
    )


@pytest.fixture
def two_seeded_orgs() -> Iterator[dict[str, dict[str, str]]]:
    """Create org A and org B, each with a user_id membership, an api_key, and an
    extraction row -- spread across 4 tables to make the cascade proof meaningful.
    Any rows NOT deleted by the test itself are cleaned up on teardown.
    """
    orgs: dict[str, dict[str, str]] = {}
    conn = _conn()
    try:
        cur = conn.cursor()
        for label in ("a", "b"):
            org_id = str(uuid.uuid4())
            user_id = str(uuid.uuid4())
            slug = f"del-test-{label}-{org_id[:8]}"
            cur.execute(
                "INSERT INTO public.organizations (id, name, slug) VALUES (%s, %s, %s)",
                (org_id, f"Deletion Test Org {label.upper()}", slug),
            )
            cur.execute(
                "INSERT INTO public.organization_members (org_id, user_id, role) "
                "VALUES (%s, %s, 'owner')",
                (org_id, user_id),
            )
            cur.execute(
                "INSERT INTO public.api_keys (org_id, key_hash, key_prefix, name, quota) "
                "VALUES (%s, %s, %s, 'del-test-key', 100)",
                (org_id, f"fake-hash-{label}", f"riq_live_deltest{label}"),
            )
            orgs[label] = {"org_id": org_id, "user_id": user_id, "slug": slug}
        conn.commit()

        input_hash_a = f"sha256:{uuid.uuid4().hex}{uuid.uuid4().hex}"[:71]
        input_hash_b = f"sha256:{uuid.uuid4().hex}{uuid.uuid4().hex}"[:71]
        save_extraction_pg(
            orgs["a"]["org_id"], None, input_hash_a, "review text for org a",
            _make_extraction(orgs["a"]["org_id"], input_hash_a),
            "mock-model", "v2.0", "1.0.0", 10, False,
        )
        save_extraction_pg(
            orgs["b"]["org_id"], None, input_hash_b, "review text for org b",
            _make_extraction(orgs["b"]["org_id"], input_hash_b),
            "mock-model", "v2.0", "1.0.0", 10, False,
        )
        orgs["a"]["input_hash"] = input_hash_a
        orgs["b"]["input_hash"] = input_hash_b
    finally:
        conn.close()

    yield orgs

    # Teardown: delete whichever orgs the test didn't already delete itself.
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM public.organizations WHERE id IN (%s, %s)",
            (orgs["a"]["org_id"], orgs["b"]["org_id"]),
        )
        conn.commit()
    finally:
        conn.close()


def _table_row_count(table: str, org_id: str) -> int:
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM public.{table} WHERE org_id = %s", (org_id,))  # noqa: S608
        (count,) = cur.fetchone()
        return int(count)
    finally:
        conn.close()


@pytest.mark.integration
class TestAccountDeletion:
    def test_delete_removes_org_a_completely_across_every_table(
        self, two_seeded_orgs: dict[str, dict[str, str]]
    ) -> None:
        org_a = two_seeded_orgs["a"]

        _do_delete_org(org_a["user_id"], org_a["slug"])

        for table in ("organization_members", "api_keys", "extractions"):
            assert _table_row_count(table, org_a["org_id"]) == 0, (
                f"org A's rows in {table} survived deletion"
            )

        conn = _conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM public.organizations WHERE id = %s", (org_a["org_id"],))
            assert cur.fetchone() is None, "org A row itself survived deletion"
        finally:
            conn.close()

    def test_delete_org_a_does_not_touch_org_b(
        self, two_seeded_orgs: dict[str, dict[str, str]]
    ) -> None:
        org_a, org_b = two_seeded_orgs["a"], two_seeded_orgs["b"]

        _do_delete_org(org_a["user_id"], org_a["slug"])

        for table in ("organization_members", "api_keys", "extractions"):
            assert _table_row_count(table, org_b["org_id"]) == 1, (
                f"org B's row in {table} was affected by org A's deletion"
            )

        conn = _conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM public.organizations WHERE id = %s", (org_b["org_id"],))
            assert cur.fetchone() is not None, "org B row was deleted along with org A"
        finally:
            conn.close()

        ext_b = get_by_hash_pg(org_b["org_id"], org_b["input_hash"])
        assert ext_b is not None, "org B's extraction is still fully readable after org A's deletion"

    def test_wrong_slug_deletes_neither_org(
        self, two_seeded_orgs: dict[str, dict[str, str]]
    ) -> None:
        org_a, org_b = two_seeded_orgs["a"], two_seeded_orgs["b"]

        with pytest.raises(HTTPException) as exc_info:
            _do_delete_org(org_a["user_id"], "not-the-right-slug")
        assert exc_info.value.status_code == 400

        conn = _conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM public.organizations WHERE id IN (%s, %s)",
                (org_a["org_id"], org_b["org_id"]),
            )
            (count,) = cur.fetchone()
            assert count == 2, "a rejected (wrong-slug) delete attempt must delete nothing"
        finally:
            conn.close()

    def test_org_bs_slug_cannot_be_used_to_delete_org_a(
        self, two_seeded_orgs: dict[str, dict[str, str]]
    ) -> None:
        """Even knowing org B's exact slug, org A's own caller cannot use it as
        confirm_slug -- the check is always against the CALLER's own resolved org."""
        org_a, org_b = two_seeded_orgs["a"], two_seeded_orgs["b"]

        with pytest.raises(HTTPException) as exc_info:
            _do_delete_org(org_a["user_id"], org_b["slug"])
        assert exc_info.value.status_code == 400

        conn = _conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM public.organizations WHERE id IN (%s, %s)",
                (org_a["org_id"], org_b["org_id"]),
            )
            (count,) = cur.fetchone()
            assert count == 2, "neither org may be deleted by this mismatched attempt"
        finally:
            conn.close()
