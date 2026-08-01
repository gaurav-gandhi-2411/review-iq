"""Unit tests for app.auth.signup — provision endpoint."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import psycopg2.errors
import pytest
from app.auth.signup import _get_org_for_user, _provision_org_and_key, _ProvisionRaceLost
from app.main import app
from fastapi import HTTPException
from fastapi.testclient import TestClient


class _MemberRaceCollision(psycopg2.errors.UniqueViolation):
    """A UniqueViolation faking organization_members_user_id_key -- psycopg2's real
    diag is populated by the C extension from a live connection and isn't writable
    on a bare instance, so this subclass overrides `diag` to fake it for tests."""

    @property
    def diag(self) -> SimpleNamespace:
        return SimpleNamespace(constraint_name="organization_members_user_id_key")


class _OtherUniqueViolation(psycopg2.errors.UniqueViolation):
    @property
    def diag(self) -> SimpleNamespace:
        return SimpleNamespace(constraint_name="organizations_slug_key")


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


def test_provision_race_lost_falls_back_to_existing() -> None:
    """Two concurrent first-logins for the same user_id: this call's org INSERT
    committed first at the organizations table, but organization_members.user_id's
    UNIQUE constraint means only one membership row can win. The loser must return
    the winner's org as "existing", not a raw 500."""
    winner_org = {
        "org_id": str(uuid.uuid4()),
        "key_prefix": "riq_live_winner1",
        "quota": 100,
    }
    with (
        patch(
            "app.auth.signup.verify_supabase_jwt",
            new=AsyncMock(return_value=_fake_user()),
        ),
        patch("app.auth.signup._get_org_for_user", side_effect=[None, winner_org]),
        patch(
            "app.auth.signup._provision_org_and_key",
            side_effect=_ProvisionRaceLost(_USER_ID),
        ),
    ):
        resp = _make_client().post("/auth/provision", headers={"Authorization": _BEARER})

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "existing"
    assert data["org_id"] == winner_org["org_id"]
    assert "raw_key" not in data


def test_provision_race_lost_but_no_winner_row_returns_409() -> None:
    """Should be unreachable in practice (the UNIQUE violation means a committed row
    exists), but must fail loudly rather than silently if it ever happens."""
    with (
        patch(
            "app.auth.signup.verify_supabase_jwt",
            new=AsyncMock(return_value=_fake_user()),
        ),
        patch("app.auth.signup._get_org_for_user", side_effect=[None, None]),
        patch(
            "app.auth.signup._provision_org_and_key",
            side_effect=_ProvisionRaceLost(_USER_ID),
        ),
    ):
        resp = _make_client().post("/auth/provision", headers={"Authorization": _BEARER})

    assert resp.status_code == 409


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
    """Two-step resolution (BYPASSRLS remediation 2c): resolve_org_for_user(...) first,
    then _set_tenant(), then the RLS-scoped key_prefix/quota read."""
    org_id = uuid.uuid4()
    cur = MagicMock()
    cur.fetchone.side_effect = [
        (org_id,),  # resolve_org_for_user(...)
        ("riq_live_abc1234", 100),  # key_prefix, quota
    ]
    conn = MagicMock()
    conn.cursor.return_value = cur
    with patch("app.auth.signup._db_connect", return_value=conn):
        with patch("app.auth.signup._set_tenant") as mock_set_tenant:
            result = _get_org_for_user(_USER_ID)
    assert result is not None
    assert result["org_id"] == str(org_id)
    assert result["key_prefix"] == "riq_live_abc1234"
    assert result["quota"] == 100
    first_call_sql = cur.execute.call_args_list[0][0][0]
    assert "resolve_org_for_user" in first_call_sql
    mock_set_tenant.assert_called_once_with(cur, str(org_id))


def test_get_org_for_user_unresolved_user_returns_none_without_set_tenant() -> None:
    """A user with no membership row yet must never reach _set_tenant() -- there's no
    org_id to scope to."""
    cur = MagicMock()
    cur.fetchone.return_value = (None,)
    conn = MagicMock()
    conn.cursor.return_value = cur
    with patch("app.auth.signup._db_connect", return_value=conn):
        with patch("app.auth.signup._set_tenant") as mock_set_tenant:
            result = _get_org_for_user(_USER_ID)
    assert result is None
    mock_set_tenant.assert_not_called()


def test_get_org_for_user_rolls_back_on_exception() -> None:
    conn = MagicMock()
    conn.cursor.return_value.execute.side_effect = RuntimeError("db error")
    with patch("app.auth.signup._db_connect", return_value=conn):
        with pytest.raises(RuntimeError, match="db error"):
            _get_org_for_user(_USER_ID)
    conn.rollback.assert_called_once()
    conn.close.assert_called_once()


def test_provision_org_and_key_returns_raw_key_and_creates_rows() -> None:
    """Both writes now go through SECURITY DEFINER functions (BYPASSRLS remediation
    2c) -- create_org_and_membership(...) returns org_id, create_api_key_for_org(...)
    returns (id, created_at). No raw INSERT into organizations/organization_members/
    api_keys from this module anymore."""
    org_id = uuid.uuid4()
    key_row_id = uuid.uuid4()
    cur = MagicMock()
    cur.fetchone.side_effect = [
        (org_id,),  # create_org_and_membership(...)
        (key_row_id, "2026-08-01T00:00:00Z"),  # create_api_key_for_org(...)
    ]
    conn = MagicMock()
    conn.cursor.return_value = cur

    with patch("app.auth.signup._db_connect", return_value=conn):
        result = _provision_org_and_key(_USER_ID, _EMAIL)

    assert result["org_id"] == str(org_id)
    assert str(result["key_prefix"]).startswith("riq_live_")
    assert str(result["raw_key"]).startswith("riq_live_")
    assert result["monthly_quota"] == 100
    all_sqls = [c[0][0] for c in cur.execute.call_args_list]
    assert "create_org_and_membership" in all_sqls[0]
    # SAVEPOINT/RELEASE bracket the retry-scoped insert (app/auth/keygen.py) -- find the
    # actual insert call by content, not a fixed index.
    assert any("create_api_key_for_org" in sql for sql in all_sqls)
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


def test_provision_org_and_key_member_race_raises_race_lost_and_rolls_back() -> None:
    """create_org_and_membership(...) now does the organizations + organization_members
    inserts atomically inside the function itself (BYPASSRLS remediation 2c) -- a
    concurrent-signup race raises the SAME UniqueViolation from this ONE call instead
    of a standalone organization_members INSERT; _ProvisionRaceLost handling is
    unchanged."""
    cur = MagicMock()

    def _execute(sql: str, *args: object) -> None:
        if "create_org_and_membership" in sql:
            raise _MemberRaceCollision()

    cur.execute.side_effect = _execute
    conn = MagicMock()
    conn.cursor.return_value = cur

    with patch("app.auth.signup._db_connect", return_value=conn):
        with pytest.raises(_ProvisionRaceLost):
            _provision_org_and_key(_USER_ID, _EMAIL)

    # The whole transaction rolls back -- no orphaned org row survives the losing
    # side of the race (create_org_and_membership's own INSERTs are atomic and this
    # outer rollback covers it either way).
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()
    conn.close.assert_called_once()


def test_provision_org_and_key_non_race_unique_violation_propagates() -> None:
    """A UniqueViolation on a *different* constraint (e.g. organizations.slug) must
    never be mistaken for the user_id race and swallowed."""
    cur = MagicMock()

    def _execute(sql: str, *args: object) -> None:
        if "create_org_and_membership" in sql:
            raise _OtherUniqueViolation()

    cur.execute.side_effect = _execute
    conn = MagicMock()
    conn.cursor.return_value = cur

    with patch("app.auth.signup._db_connect", return_value=conn):
        with pytest.raises(_OtherUniqueViolation):
            _provision_org_and_key(_USER_ID, _EMAIL)

    conn.rollback.assert_called_once()
