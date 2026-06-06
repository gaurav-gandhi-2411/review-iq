"""Unit tests for app.auth.signup — provision endpoint."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.auth.signup import _get_org_for_user, _provision_org_and_key
from app.main import app
from fastapi import HTTPException
from fastapi.testclient import TestClient

_USER_ID = str(uuid.uuid4())
_EMAIL = "test@example.com"
_BEARER = "Bearer fake-jwt-token"


def _fake_user() -> MagicMock:
    u = MagicMock()
    u.id = _USER_ID
    u.email = _EMAIL
    return u


def _make_client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Router endpoint tests
# ---------------------------------------------------------------------------


def test_provision_first_login_returns_created_with_raw_key() -> None:
    with (
        patch(
            "app.auth.signup.verify_supabase_jwt",
            new=AsyncMock(return_value=_fake_user()),
        ),
        patch("app.auth.signup._get_org_for_user", return_value=None),
        patch(
            "app.auth.signup._provision_org_and_key",
            return_value={
                "org_id": str(uuid.uuid4()),
                "key_prefix": "riq_live_abc1234",
                "raw_key": "riq_live_" + "a" * 32,
                "monthly_quota": 100,
            },
        ),
    ):
        resp = _make_client().post("/auth/provision", headers={"Authorization": _BEARER})

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "created"
    assert "raw_key" in data
    assert data["monthly_quota"] == 100


def test_provision_existing_user_returns_existing_no_raw_key() -> None:
    with (
        patch(
            "app.auth.signup.verify_supabase_jwt",
            new=AsyncMock(return_value=_fake_user()),
        ),
        patch(
            "app.auth.signup._get_org_for_user",
            return_value={
                "org_id": str(uuid.uuid4()),
                "key_prefix": "riq_live_abc1234",
                "quota": 100,
            },
        ),
    ):
        resp = _make_client().post("/auth/provision", headers={"Authorization": _BEARER})

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "existing"
    assert "raw_key" not in data
    assert "key_prefix" in data


def test_provision_missing_bearer_returns_401() -> None:
    resp = _make_client().post("/auth/provision", headers={"Authorization": "Basic xyz"})
    assert resp.status_code == 401


def test_provision_invalid_jwt_returns_401() -> None:
    with patch(
        "app.auth.signup.verify_supabase_jwt",
        new=AsyncMock(
            side_effect=HTTPException(status_code=401, detail="Invalid or expired Supabase token.")
        ),
    ):
        resp = _make_client().post("/auth/provision", headers={"Authorization": _BEARER})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Unit tests for sync DB helpers (mocked psycopg2)
# ---------------------------------------------------------------------------


def _make_mock_conn(fetchone_return: object = None) -> MagicMock:
    """Build a minimal psycopg2 connection mock."""
    cur = MagicMock()
    cur.fetchone.return_value = fetchone_return
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


def test_get_org_for_user_returns_none_when_no_row() -> None:
    conn = _make_mock_conn(fetchone_return=None)
    with patch("app.auth.signup._db_connect", return_value=conn):
        result = _get_org_for_user(_USER_ID)
    assert result is None
    conn.commit.assert_called_once()
    conn.close.assert_called_once()


def test_get_org_for_user_returns_dict_when_row_exists() -> None:
    org_id = uuid.uuid4()
    conn = _make_mock_conn(fetchone_return=(org_id, "riq_live_abc1234", 100))
    with patch("app.auth.signup._db_connect", return_value=conn):
        result = _get_org_for_user(_USER_ID)
    assert result is not None
    assert result["org_id"] == str(org_id)
    assert result["key_prefix"] == "riq_live_abc1234"
    assert result["quota"] == 100


def test_get_org_for_user_rolls_back_on_exception() -> None:
    conn = MagicMock()
    conn.cursor.return_value.execute.side_effect = RuntimeError("db error")
    with patch("app.auth.signup._db_connect", return_value=conn):
        with pytest.raises(RuntimeError, match="db error"):
            _get_org_for_user(_USER_ID)
    conn.rollback.assert_called_once()
    conn.close.assert_called_once()


def test_provision_org_and_key_returns_raw_key_and_creates_rows() -> None:
    org_id = uuid.uuid4()
    cur = MagicMock()
    # fetchone returns org_id for the INSERT ... RETURNING id
    cur.fetchone.return_value = (org_id,)
    conn = MagicMock()
    conn.cursor.return_value = cur

    with patch("app.auth.signup._db_connect", return_value=conn):
        result = _provision_org_and_key(_USER_ID, _EMAIL)

    assert result["org_id"] == str(org_id)
    assert str(result["key_prefix"]).startswith("riq_live_")
    assert str(result["raw_key"]).startswith("riq_live_")
    assert result["monthly_quota"] == 100
    conn.commit.assert_called_once()
    conn.close.assert_called_once()


def test_provision_org_and_key_rolls_back_on_exception() -> None:
    conn = MagicMock()
    conn.cursor.return_value.execute.side_effect = RuntimeError("insert failed")
    with patch("app.auth.signup._db_connect", return_value=conn):
        with pytest.raises(RuntimeError, match="insert failed"):
            _provision_org_and_key(_USER_ID, _EMAIL)
    conn.rollback.assert_called_once()
    conn.close.assert_called_once()
