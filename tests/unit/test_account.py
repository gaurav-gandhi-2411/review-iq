"""Unit tests for app.api.account — account info and key regeneration."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.api.account import _do_delete_org, _do_regenerate, _fetch_account, _get_org_id_and_slug
from app.main import app
from fastapi import HTTPException
from fastapi.testclient import TestClient

_USER_ID = str(uuid.uuid4())
_ORG_ID = str(uuid.uuid4())
_BEARER = "Bearer fake-jwt-token"


def _fake_user() -> MagicMock:
    u = MagicMock()
    u.id = _USER_ID
    return u


def _make_client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Router endpoint tests
# ---------------------------------------------------------------------------


def test_get_account_returns_200_with_usage() -> None:
    with (
        patch(
            "app.api.account.verify_supabase_jwt",
            new=AsyncMock(return_value=_fake_user()),
        ),
        patch(
            "app.api.account._fetch_account",
            return_value={
                "org_id": _ORG_ID,
                "key_prefix": "riq_live_abc1234",
                "monthly_quota": 100,
                "monthly_usage": 7,
            },
        ),
    ):
        resp = _make_client().get("/account", headers={"Authorization": _BEARER})

    assert resp.status_code == 200
    data = resp.json()
    assert data["monthly_usage"] == 7
    assert data["monthly_quota"] == 100
    assert "key_prefix" in data
    assert "raw_key" not in data  # never returned by GET /account


def test_get_account_missing_bearer_returns_401() -> None:
    resp = _make_client().get("/account", headers={"Authorization": "Token xyz"})
    assert resp.status_code == 401


def test_get_account_not_found_returns_404() -> None:
    with (
        patch(
            "app.api.account.verify_supabase_jwt",
            new=AsyncMock(return_value=_fake_user()),
        ),
        patch(
            "app.api.account._fetch_account",
            side_effect=HTTPException(status_code=404, detail="No account found."),
        ),
    ):
        resp = _make_client().get("/account", headers={"Authorization": _BEARER})
    assert resp.status_code == 404


def test_regenerate_key_returns_raw_key() -> None:
    with (
        patch(
            "app.api.account.verify_supabase_jwt",
            new=AsyncMock(return_value=_fake_user()),
        ),
        patch(
            "app.api.account._do_regenerate",
            return_value={
                "key_prefix": "riq_live_new1234",
                "raw_key": "riq_live_" + "b" * 32,
                "monthly_quota": 100,
            },
        ),
    ):
        resp = _make_client().post("/account/regenerate-key", headers={"Authorization": _BEARER})

    assert resp.status_code == 200
    data = resp.json()
    assert "raw_key" in data
    assert data["key_prefix"].startswith("riq_live_")


def test_regenerate_key_missing_bearer_returns_401() -> None:
    resp = _make_client().post("/account/regenerate-key")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Unit tests for sync DB helpers (mocked psycopg2)
# ---------------------------------------------------------------------------


def _make_mock_conn(fetchone_side_effect: list[object] | None = None) -> MagicMock:
    """Return a minimal psycopg2 connection mock with sequential fetchone returns."""
    cur = MagicMock()
    if fetchone_side_effect is not None:
        cur.fetchone.side_effect = fetchone_side_effect
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


def test_fetch_account_returns_correct_fields() -> None:
    key_id = uuid.uuid4()
    org_id = uuid.uuid4()
    # First fetchone → api_keys row; second → COUNT(*) for usage
    conn = _make_mock_conn(
        fetchone_side_effect=[
            ("riq_live_abc1234", 100, key_id, org_id, "acme-inc-a1b2c3"),
            (7,),
        ]
    )
    with patch("app.api.account._db_connect", return_value=conn):
        result = _fetch_account(_USER_ID)

    assert result["key_prefix"] == "riq_live_abc1234"
    assert result["monthly_quota"] == 100
    assert result["monthly_usage"] == 7
    assert result["org_id"] == str(org_id)
    assert result["slug"] == "acme-inc-a1b2c3"
    conn.commit.assert_called_once()
    conn.close.assert_called_once()


def test_fetch_account_raises_404_when_no_row() -> None:
    conn = _make_mock_conn(fetchone_side_effect=[None])
    with patch("app.api.account._db_connect", return_value=conn):
        with pytest.raises(HTTPException) as exc_info:
            _fetch_account(_USER_ID)
    assert exc_info.value.status_code == 404
    conn.close.assert_called_once()


def test_fetch_account_rolls_back_on_db_error() -> None:
    conn = MagicMock()
    conn.cursor.return_value.execute.side_effect = RuntimeError("query failed")
    with patch("app.api.account._db_connect", return_value=conn):
        with pytest.raises(RuntimeError, match="query failed"):
            _fetch_account(_USER_ID)
    conn.rollback.assert_called_once()
    conn.close.assert_called_once()


def test_do_regenerate_revokes_old_and_inserts_new() -> None:
    old_key_id = uuid.uuid4()
    org_id = uuid.uuid4()
    cur = MagicMock()
    cur.fetchone.return_value = (old_key_id, org_id)
    conn = MagicMock()
    conn.cursor.return_value = cur

    with patch("app.api.account._db_connect", return_value=conn):
        result = _do_regenerate(_USER_ID)

    assert str(result["key_prefix"]).startswith("riq_live_")
    assert str(result["raw_key"]).startswith("riq_live_")
    assert result["monthly_quota"] == 100
    conn.commit.assert_called_once()
    conn.close.assert_called_once()


def test_do_regenerate_raises_404_when_no_active_key() -> None:
    cur = MagicMock()
    cur.fetchone.return_value = None
    conn = MagicMock()
    conn.cursor.return_value = cur

    with patch("app.api.account._db_connect", return_value=conn):
        with pytest.raises(HTTPException) as exc_info:
            _do_regenerate(_USER_ID)
    assert exc_info.value.status_code == 404
    conn.close.assert_called_once()


def test_do_regenerate_rolls_back_on_db_error() -> None:
    conn = MagicMock()
    conn.cursor.return_value.execute.side_effect = RuntimeError("update failed")
    with patch("app.api.account._db_connect", return_value=conn):
        with pytest.raises(RuntimeError, match="update failed"):
            _do_regenerate(_USER_ID)
    conn.rollback.assert_called_once()
    conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# DELETE /account — self-service org deletion (audit finding #7)
# ---------------------------------------------------------------------------


def test_get_org_id_and_slug_returns_tuple() -> None:
    org_id = uuid.uuid4()
    conn = _make_mock_conn(fetchone_side_effect=[(org_id, "acme-inc-a1b2c3")])
    with patch("app.api.account._db_connect", return_value=conn):
        result = _get_org_id_and_slug(_USER_ID)
    assert result == (str(org_id), "acme-inc-a1b2c3")


def test_get_org_id_and_slug_returns_none_when_not_a_member() -> None:
    conn = _make_mock_conn(fetchone_side_effect=[None])
    with patch("app.api.account._db_connect", return_value=conn):
        result = _get_org_id_and_slug(_USER_ID)
    assert result is None


def test_do_delete_org_wrong_slug_raises_400_and_deletes_nothing() -> None:
    org_id = uuid.uuid4()
    conn = MagicMock()
    with (
        patch(
            "app.api.account._get_org_id_and_slug",
            return_value=(str(org_id), "acme-inc-a1b2c3"),
        ),
        patch("app.api.account._db_connect", return_value=conn),
    ):
        with pytest.raises(HTTPException) as exc_info:
            _do_delete_org(_USER_ID, "wrong-slug")

    assert exc_info.value.status_code == 400
    # CORE ASSERTION: a slug mismatch must never even open a connection to delete.
    conn.cursor.return_value.execute.assert_not_called()


def test_do_delete_org_no_account_raises_404() -> None:
    with patch("app.api.account._get_org_id_and_slug", return_value=None):
        with pytest.raises(HTTPException) as exc_info:
            _do_delete_org(_USER_ID, "anything")
    assert exc_info.value.status_code == 404


def test_do_delete_org_correct_slug_deletes_the_resolved_org_only() -> None:
    """CORE ASSERTION: the DELETE targets exactly the org_id resolved from the
    caller's OWN membership (_get_org_id_and_slug) -- confirm_slug is a human
    confirmation string only, never used to select which org gets deleted. There
    is no parameter anywhere in this call chain an attacker could use to target
    a different org than their own."""
    org_id = uuid.uuid4()
    cur = MagicMock()
    cur.rowcount = 1
    conn = MagicMock()
    conn.cursor.return_value = cur

    with (
        patch(
            "app.api.account._get_org_id_and_slug",
            return_value=(str(org_id), "acme-inc-a1b2c3"),
        ),
        patch("app.api.account._db_connect", return_value=conn),
    ):
        _do_delete_org(_USER_ID, "acme-inc-a1b2c3")

    cur.execute.assert_called_once_with(
        "DELETE FROM public.organizations WHERE id = %s", (str(org_id),)
    )
    conn.commit.assert_called_once()
    conn.close.assert_called_once()


def test_do_delete_org_rolls_back_on_db_error() -> None:
    conn = MagicMock()
    conn.cursor.return_value.execute.side_effect = RuntimeError("delete failed")
    with (
        patch(
            "app.api.account._get_org_id_and_slug",
            return_value=(str(uuid.uuid4()), "acme-inc-a1b2c3"),
        ),
        patch("app.api.account._db_connect", return_value=conn),
    ):
        with pytest.raises(RuntimeError, match="delete failed"):
            _do_delete_org(_USER_ID, "acme-inc-a1b2c3")
    conn.rollback.assert_called_once()
    conn.close.assert_called_once()


def test_delete_account_endpoint_wrong_slug_returns_400() -> None:
    with (
        patch(
            "app.api.account.verify_supabase_jwt",
            new=AsyncMock(return_value=_fake_user()),
        ),
        patch(
            "app.api.account._do_delete_org",
            side_effect=HTTPException(status_code=400, detail="confirm_slug does not match."),
        ),
    ):
        resp = _make_client().request(
            "DELETE",
            "/account",
            json={"confirm_slug": "wrong"},
            headers={"Authorization": _BEARER},
        )
    assert resp.status_code == 400


def test_delete_account_endpoint_correct_slug_returns_204() -> None:
    with (
        patch(
            "app.api.account.verify_supabase_jwt",
            new=AsyncMock(return_value=_fake_user()),
        ),
        patch("app.api.account._do_delete_org", return_value=None) as mock_delete,
    ):
        resp = _make_client().request(
            "DELETE",
            "/account",
            json={"confirm_slug": "acme-inc-a1b2c3"},
            headers={"Authorization": _BEARER},
        )
    assert resp.status_code == 204
    mock_delete.assert_called_once_with(_USER_ID, "acme-inc-a1b2c3")


def test_delete_account_endpoint_missing_bearer_returns_401() -> None:
    resp = _make_client().request("DELETE", "/account", json={"confirm_slug": "x"})
    assert resp.status_code == 401


def test_cannot_delete_another_orgs_account() -> None:
    """Even if an attacker somehow learned another org's slug and passed it as
    confirm_slug, _get_org_id_and_slug resolves ONLY the caller's own org from
    their verified user_id -- there is no code path where a caller-supplied slug
    or org_id selects which org's data gets deleted."""
    my_org_id = uuid.uuid4()
    cur = MagicMock()
    cur.rowcount = 1
    conn = MagicMock()
    conn.cursor.return_value = cur

    with (
        # The DB lookup only ever returns THIS user's own org, regardless of
        # what confirm_slug the caller sends.
        patch(
            "app.api.account._get_org_id_and_slug",
            return_value=(str(my_org_id), "my-own-org-slug"),
        ),
        patch("app.api.account._db_connect", return_value=conn),
    ):
        # Attacker guesses/knows another org's slug and tries to use it as confirmation.
        with pytest.raises(HTTPException) as exc_info:
            _do_delete_org(_USER_ID, "someone-elses-org-slug")

    assert exc_info.value.status_code == 400
    cur.execute.assert_not_called()  # never even attempted a delete
