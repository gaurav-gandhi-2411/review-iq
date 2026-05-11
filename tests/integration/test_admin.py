"""Integration tests for /admin/* endpoints.

Tests org creation, key generation, listing (prefix-only), rotation,
and soft-delete against a live Supabase DB.

Marked 'integration' — requires live Supabase DB and valid admin credentials in .env.
Run: uv run pytest tests/integration/test_admin.py -v -m integration
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from app.auth.api_key import _lookup_and_record
from app.main import app

load_dotenv(Path(__file__).parents[2] / ".env")

_USERNAME = os.environ["ADMIN_USERNAME"]
_PASSWORD = os.environ["TEST_ADMIN_PASSWORD"]
_AUTH = (_USERNAME, _PASSWORD)

client = TestClient(app, raise_server_exceptions=True)


def _post_org(name: str, slug: str) -> dict:
    r = client.post("/admin/organizations", json={"name": name, "slug": slug}, auth=_AUTH)
    assert r.status_code == 201, r.text
    return r.json()


def _post_key(org_id: str, name: str = "test-key", quota: int = 10) -> dict:
    r = client.post(
        f"/admin/organizations/{org_id}/keys",
        json={"name": name, "quota": quota},
        auth=_AUTH,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _delete_key(org_id: str, key_id: str) -> None:
    r = client.delete(f"/admin/organizations/{org_id}/keys/{key_id}", auth=_AUTH)
    assert r.status_code == 204, r.text


def _teardown_org(org_id: str) -> None:
    import psycopg2
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
def test_create_organization() -> None:
    import uuid
    slug = f"integ-test-{uuid.uuid4().hex[:8]}"
    r = client.post(
        "/admin/organizations",
        json={"name": "Integ Test Org", "slug": slug},
        auth=_AUTH,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["slug"] == slug
    assert body["plan"] == "free"
    assert "id" in body
    _teardown_org(body["id"])


@pytest.mark.integration
def test_create_organization_duplicate_slug_409() -> None:
    import uuid
    slug = f"dup-{uuid.uuid4().hex[:8]}"
    org = _post_org("First", slug)
    try:
        r = client.post("/admin/organizations", json={"name": "Second", "slug": slug}, auth=_AUTH)
        assert r.status_code == 409
    finally:
        _teardown_org(org["id"])


@pytest.mark.integration
def test_get_organization() -> None:
    import uuid
    org = _post_org("Get Test", f"get-{uuid.uuid4().hex[:8]}")
    try:
        r = client.get(f"/admin/organizations/{org['id']}", auth=_AUTH)
        assert r.status_code == 200
        assert r.json()["id"] == org["id"]
    finally:
        _teardown_org(org["id"])


@pytest.mark.integration
def test_get_organization_not_found() -> None:
    import uuid
    r = client.get(f"/admin/organizations/{uuid.uuid4()}", auth=_AUTH)
    assert r.status_code == 404


@pytest.mark.integration
def test_create_key_raw_key_returned_once_and_authenticates() -> None:
    """The raw key is in the response; calling _lookup_and_record with it succeeds."""
    import uuid
    org = _post_org("Key Test", f"key-{uuid.uuid4().hex[:8]}")
    try:
        key_data = _post_key(org["id"])
        raw_key = key_data["raw_key"]
        assert raw_key.startswith("riq_live_")
        assert key_data["note"]  # "Store this key securely..."

        ctx = _lookup_and_record(raw_key)
        assert ctx.org_id == org["id"]
        assert ctx.api_key_id == key_data["id"]
    finally:
        _teardown_org(org["id"])


@pytest.mark.integration
def test_list_keys_returns_prefix_never_hash() -> None:
    """Listed keys include key_prefix but must not expose key_hash or raw_key."""
    import uuid
    org = _post_org("List Test", f"list-{uuid.uuid4().hex[:8]}")
    try:
        _post_key(org["id"])
        r = client.get(f"/admin/organizations/{org['id']}/keys", auth=_AUTH)
        assert r.status_code == 200
        keys = r.json()["keys"]
        assert len(keys) == 1
        k = keys[0]
        assert "key_prefix" in k
        assert k["key_prefix"].startswith("riq_live_")
        assert "key_hash" not in k
        assert "raw_key" not in k
        assert k["revoked_at"] is None
    finally:
        _teardown_org(org["id"])


@pytest.mark.integration
def test_rotate_key_old_fails_new_succeeds() -> None:
    """After rotation, the old key returns 401; the new raw key authenticates."""
    import uuid
    from fastapi import HTTPException

    org = _post_org("Rotate Test", f"rot-{uuid.uuid4().hex[:8]}")
    try:
        old = _post_key(org["id"])
        old_raw = old["raw_key"]

        # Old key works before rotation
        ctx = _lookup_and_record(old_raw)
        assert ctx.api_key_id == old["id"]

        # Rotate
        r = client.post(
            f"/admin/organizations/{org['id']}/keys/{old['id']}/rotate",
            auth=_AUTH,
        )
        assert r.status_code == 201
        new_data = r.json()
        new_raw = new_data["raw_key"]
        assert new_raw != old_raw

        # Old key is now rejected
        with pytest.raises(HTTPException) as exc:
            _lookup_and_record(old_raw)
        assert exc.value.status_code == 401

        # New key authenticates
        ctx2 = _lookup_and_record(new_raw)
        assert ctx2.api_key_id == new_data["id"]
    finally:
        _teardown_org(org["id"])


@pytest.mark.integration
def test_rotate_already_revoked_key_404() -> None:
    import uuid
    org = _post_org("Rot404 Test", f"rot404-{uuid.uuid4().hex[:8]}")
    try:
        key = _post_key(org["id"])
        _delete_key(org["id"], key["id"])
        r = client.post(
            f"/admin/organizations/{org['id']}/keys/{key['id']}/rotate",
            auth=_AUTH,
        )
        assert r.status_code == 404
    finally:
        _teardown_org(org["id"])


@pytest.mark.integration
def test_delete_key_soft_deletes() -> None:
    """DELETE sets revoked_at; the key appears in LIST with revoked_at set."""
    import uuid
    org = _post_org("Del Test", f"del-{uuid.uuid4().hex[:8]}")
    try:
        key = _post_key(org["id"])
        _delete_key(org["id"], key["id"])

        # Key still appears in list but is marked revoked
        r = client.get(f"/admin/organizations/{org['id']}/keys", auth=_AUTH)
        keys = r.json()["keys"]
        assert len(keys) == 1
        assert keys[0]["revoked_at"] is not None

        # Key no longer authenticates
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _lookup_and_record(key["raw_key"])
        assert exc.value.status_code == 401
    finally:
        _teardown_org(org["id"])


@pytest.mark.integration
def test_delete_already_revoked_key_404() -> None:
    import uuid
    org = _post_org("Del404 Test", f"del404-{uuid.uuid4().hex[:8]}")
    try:
        key = _post_key(org["id"])
        _delete_key(org["id"], key["id"])
        r = client.delete(f"/admin/organizations/{org['id']}/keys/{key['id']}", auth=_AUTH)
        assert r.status_code == 404
    finally:
        _teardown_org(org["id"])


@pytest.mark.integration
def test_admin_wrong_credentials_returns_401() -> None:
    r = client.get("/admin/organizations/00000000-0000-0000-0000-000000000000", auth=("admin", "wrong"))
    assert r.status_code == 401
