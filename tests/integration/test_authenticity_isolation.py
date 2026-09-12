"""Cross-tenant isolation tests for authenticity_audits.

Tests BOTH read isolation and insert isolation (WITH CHECK enforcement).
Requires: live Supabase DB, SUPABASE_DIRECT_URL in env.
Run: uv run pytest tests/integration/test_authenticity_isolation.py -v -m integration
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

from app.core.storage_pg import (  # noqa: E402
    count_authenticity_audits_pg,
    save_authenticity_audit_pg,
)

# ---------------------------------------------------------------------------
# Helpers
#
# Orgs are seeded via a direct DB write, never through /admin/organizations --
# that endpoint is only mounted by the separate admin service (ADR 0006) and was
# never actually reachable from this file's own TestClient import of app.main.app
# (a module-level singleton that mounts admin_router XOR the public routes this
# file doesn't even use, based on SERVICE_ROLE at import time). This file never
# hits the HTTP API at all -- it tests RLS directly via app/core/storage_pg.py
# and raw psycopg2, so an app instance was never actually needed here.
# ---------------------------------------------------------------------------


def _create_org(suffix: str) -> dict:
    org_id = str(uuid.uuid4())
    slug = f"audit-{suffix}-{uuid.uuid4().hex[:6]}"
    conn = psycopg2.connect(os.environ["SUPABASE_DIRECT_URL"])
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO public.organizations (id, name, slug) VALUES (%s, %s, %s)",
            (org_id, f"Audit {suffix}", slug),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": org_id, "name": f"Audit {suffix}", "slug": slug}


def _teardown_org(org_id: str) -> None:
    """Hard-delete the org (and cascade) directly via service-role connection."""
    conn = psycopg2.connect(os.environ["SUPABASE_DIRECT_URL"])
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM public.organizations WHERE id = %s", (org_id,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_read_isolation() -> None:
    """Audit rows written for org A are invisible to org B (RLS USING check)."""
    org_a = _create_org("a")
    org_b = _create_org("b")
    org_a_id: str = org_a["id"]
    org_b_id: str = org_b["id"]

    try:
        # Write one audit row for org A
        save_authenticity_audit_pg(
            org_id=org_a_id,
            review_hash="abc123",
            score=0.9,
            label="genuine",
            flags=[],
        )

        # Org A can see its own row
        count_a = count_authenticity_audits_pg(org_a_id)
        assert count_a >= 1, f"Org A should see at least 1 audit row; got {count_a}"

        # Org B sees nothing (RLS blocks cross-tenant reads)
        count_b = count_authenticity_audits_pg(org_b_id)
        assert count_b == 0, f"Org B should see 0 audit rows; got {count_b} (isolation failure)"

    finally:
        _teardown_org(org_a_id)
        _teardown_org(org_b_id)


@pytest.mark.integration
def test_insert_isolation_with_check() -> None:
    """WITH CHECK prevents inserting a row whose org_id differs from the tenant context."""
    org_a = _create_org("wc-a")
    org_b = _create_org("wc-b")
    org_a_id: str = org_a["id"]
    org_b_id: str = org_b["id"]

    try:
        # Open a direct psycopg2 connection (same path as _set_tenant, but we set
        # tenant context to org B while attempting to write a row for org A).
        conn = psycopg2.connect(os.environ["SUPABASE_DIRECT_URL"])
        try:
            cur = conn.cursor()
            # Activate org B's RLS context
            cur.execute("SET LOCAL ROLE authenticated")
            cur.execute('SET LOCAL "app.current_org_id" = %s', (org_b_id,))

            # Attempt a cross-tenant insert: row claims org_a_id but context is org_b_id.
            # RLS WITH CHECK (org_id = public.current_org_id()) must reject this.
            with pytest.raises(psycopg2.Error):
                cur.execute(
                    "INSERT INTO public.authenticity_audits"
                    " (org_id, review_hash, score, label, flags)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    (org_a_id, "bad_hash", 0.5, "fake", "[]"),
                )
                # Force execution to the server so the policy fires
                conn.commit()

        finally:
            conn.rollback()
            conn.close()

    finally:
        _teardown_org(org_a_id)
        _teardown_org(org_b_id)
