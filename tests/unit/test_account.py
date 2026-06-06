"""Unit tests for app.api.account — account info and key regeneration."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.api.account import _do_regenerate, _fetch_account
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
        resp = _make_client().post(
            "/account/regenerate-key", headers={"Authorization": _BEARER}
        )

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
            ("riq_live_abc1234", 100, key_id, org_id),
            (7,),
        ]
    )
    with patch("app.api.account._db_connect", return_value=conn):
        result = _fetch_account(_USER_ID)

    assert result["key_prefix"] == "riq_live_abc1234"
    assert result["monthly_quota"] == 100
    assert result["monthly_usage"] == 7
    assert result["org_id"] == str(org_id)
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
