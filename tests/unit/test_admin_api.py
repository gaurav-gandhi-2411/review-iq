"""Unit tests for app.api.admin DB helper functions."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import psycopg2
import pytest
from fastapi import HTTPException

from app.api.admin import (
    _create_key_db,
    _create_org_db,
    _get_org_db,
    _list_keys_db,
    _revoke_key_db,
    _rotate_key_db,
)

_ORG_ID = str(uuid.uuid4())
_KEY_ID = str(uuid.uuid4())
_NEW_KEY_ID = str(uuid.uuid4())
_NOW = datetime.now(tz=timezone.utc)

_ORG_ROW = (uuid.UUID(_ORG_ID), "Acme", "acme", "free", _NOW)
_KEY_ROW = (uuid.UUID(_KEY_ID), "default", "riq_live_aabbccdd", 1000, _NOW, None, None)


def _make_conn() -> tuple[MagicMock, MagicMock]:
    cur = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


# ---------------------------------------------------------------------------
# _create_org_db
# ---------------------------------------------------------------------------


def test_create_org_returns_org_out() -> None:
    conn, cur = _make_conn()
    cur.fetchone.return_value = _ORG_ROW

    with patch("app.api.admin._db_connect", return_value=conn):
        org = _create_org_db("Acme", "acme", "free")

    assert org.name == "Acme"
    assert org.slug == "acme"
    conn.commit.assert_called_once()


def test_create_org_duplicate_slug_raises_409() -> None:
    conn, cur = _make_conn()
    cur.execute.side_effect = psycopg2.errors.UniqueViolation()

    with patch("app.api.admin._db_connect", return_value=conn):
        with pytest.raises(HTTPException) as exc:
            _create_org_db("Acme", "acme", "free")

    assert exc.value.status_code == 409
    conn.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# _get_org_db
# ---------------------------------------------------------------------------


def test_get_org_returns_org_out() -> None:
    conn, cur = _make_conn()
    cur.fetchone.return_value = _ORG_ROW

    with patch("app.api.admin._db_connect", return_value=conn):
        org = _get_org_db(_ORG_ID)

    assert org.id == _ORG_ID


def test_get_org_not_found_raises_404() -> None:
    conn, cur = _make_conn()
    cur.fetchone.return_value = None

    with patch("app.api.admin._db_connect", return_value=conn):
        with pytest.raises(HTTPException) as exc:
            _get_org_db(_ORG_ID)

    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# _create_key_db
# ---------------------------------------------------------------------------


def test_create_key_returns_raw_key() -> None:
    conn, cur = _make_conn()
    # SELECT org check → org exists; INSERT → returns new key row
    cur.fetchone.side_effect = [(_ORG_ID,), (uuid.UUID(_NEW_KEY_ID), _NOW)]

    with patch("app.api.admin._db_connect", return_value=conn):
        with patch("app.api.admin.generate_api_key", return_value=("riq_live_" + "a" * 32, "riq_live_aaaaaaaa", "hash")):
            result = _create_key_db(_ORG_ID, "default", 1000)

    assert result.raw_key == "riq_live_" + "a" * 32
    assert result.key_prefix == "riq_live_aaaaaaaa"
    conn.commit.assert_called_once()


def test_create_key_org_not_found_raises_404() -> None:
    conn, cur = _make_conn()
    cur.fetchone.return_value = None  # org SELECT returns nothing

    with patch("app.api.admin._db_connect", return_value=conn):
        with patch("app.api.admin.generate_api_key", return_value=("riq_live_" + "a" * 32, "riq_live_aaaaaaaa", "hash")):
            with pytest.raises(HTTPException) as exc:
                _create_key_db(_ORG_ID, "default", 1000)

    assert exc.value.status_code == 404
    conn.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# _list_keys_db
# ---------------------------------------------------------------------------


def test_list_keys_returns_prefixes_only() -> None:
    conn, cur = _make_conn()
    cur.fetchone.return_value = (_ORG_ID,)  # org exists
    cur.fetchall.return_value = [_KEY_ROW]

    with patch("app.api.admin._db_connect", return_value=conn):
        keys = _list_keys_db(_ORG_ID)

    assert len(keys) == 1
    assert keys[0].key_prefix == "riq_live_aabbccdd"
    # Verify no hash or raw key in the output model
    assert not hasattr(keys[0], "key_hash")
    assert not hasattr(keys[0], "raw_key")


def test_list_keys_org_not_found_raises_404() -> None:
    conn, cur = _make_conn()
    cur.fetchone.return_value = None

    with patch("app.api.admin._db_connect", return_value=conn):
        with pytest.raises(HTTPException) as exc:
            _list_keys_db(_ORG_ID)

    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# _rotate_key_db
# ---------------------------------------------------------------------------


def test_rotate_key_revokes_old_issues_new() -> None:
    conn, cur = _make_conn()
    # SELECT old key FOR UPDATE → found; SELECT name/quota → old_name, old_quota; INSERT → new key row
    cur.fetchone.side_effect = [
        (uuid.UUID(_KEY_ID),),         # lock old key
        ("default", 1000),              # old name + quota
        (uuid.UUID(_NEW_KEY_ID), _NOW), # new key RETURNING
    ]

    with patch("app.api.admin._db_connect", return_value=conn):
        with patch("app.api.admin.generate_api_key", return_value=("riq_live_" + "b" * 32, "riq_live_bbbbbbbb", "new_hash")):
            result = _rotate_key_db(_ORG_ID, _KEY_ID)

    assert result.raw_key == "riq_live_" + "b" * 32
    assert result.id == _NEW_KEY_ID
    # UPDATE (revoke) was called
    update_sqls = [c[0][0] for c in cur.execute.call_args_list]
    assert any("UPDATE" in s and "revoked_at" in s for s in update_sqls)
    conn.commit.assert_called_once()


def test_rotate_key_not_found_raises_404() -> None:
    conn, cur = _make_conn()
    cur.fetchone.return_value = None  # lock SELECT returns nothing

    with patch("app.api.admin._db_connect", return_value=conn):
        with patch("app.api.admin.generate_api_key", return_value=("riq_live_" + "b" * 32, "riq_live_bbbbbbbb", "h")):
            with pytest.raises(HTTPException) as exc:
                _rotate_key_db(_ORG_ID, _KEY_ID)

    assert exc.value.status_code == 404
    conn.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# _revoke_key_db
# ---------------------------------------------------------------------------


def test_revoke_key_success() -> None:
    conn, cur = _make_conn()
    cur.fetchone.return_value = (uuid.UUID(_KEY_ID),)  # RETURNING id

    with patch("app.api.admin._db_connect", return_value=conn):
        _revoke_key_db(_ORG_ID, _KEY_ID)  # must not raise

    conn.commit.assert_called_once()


def test_revoke_key_not_found_raises_404() -> None:
    conn, cur = _make_conn()
    cur.fetchone.return_value = None  # RETURNING returns nothing

    with patch("app.api.admin._db_connect", return_value=conn):
        with pytest.raises(HTTPException) as exc:
            _revoke_key_db(_ORG_ID, _KEY_ID)

    assert exc.value.status_code == 404
    conn.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# Error paths — generic exception handler (catch-all except blocks)
# ---------------------------------------------------------------------------


def test_create_org_generic_exception_rolls_back() -> None:
    conn, cur = _make_conn()
    cur.execute.side_effect = Exception("disk full")  # not a UniqueViolation

    with patch("app.api.admin._db_connect", return_value=conn):
        with pytest.raises(Exception, match="disk full"):
            _create_org_db("Acme", "acme", "free")

    conn.rollback.assert_called_once()
    conn.close.assert_called_once()


def test_create_key_generic_exception_rolls_back() -> None:
    conn, cur = _make_conn()
    # org SELECT succeeds, INSERT raises a generic error
    cur.fetchone.side_effect = [(_ORG_ID,), Exception("constraint violated")]

    with patch("app.api.admin._db_connect", return_value=conn):
        with patch("app.api.admin.generate_api_key", return_value=("riq_live_" + "a" * 32, "riq_live_aaaaaaaa", "hash")):
            with pytest.raises(Exception):
                _create_key_db(_ORG_ID, "default", 1000)

    conn.rollback.assert_called_once()
    conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# _db_connect — the actual DB connect function body
# ---------------------------------------------------------------------------


def test_admin_db_connect_uses_supabase_database_url() -> None:
    with patch("app.api.admin.get_settings") as mock_settings, \
         patch("app.api.admin.psycopg2") as mock_psycopg2:
        mock_settings.return_value.supabase_database_url = "postgresql://user:pw@host/db"
        mock_psycopg2.connect.return_value = MagicMock()
        from app.api.admin import _db_connect
        _db_connect()
    mock_psycopg2.connect.assert_called_once_with("postgresql://user:pw@host/db")
