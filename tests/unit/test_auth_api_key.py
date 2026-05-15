"""Unit tests for app.auth.api_key and app.auth.keygen."""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.auth.api_key import ApiKeyContext, _KEY_PREFIX_LEN, _lookup_and_record, require_api_key
from app.auth.keygen import generate_api_key

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

_PH = PasswordHasher()
_ORG_ID = uuid.uuid4()
_KEY_ID = uuid.uuid4()
_USAGE_ID = uuid.uuid4()
_VALID_RAW_KEY = f"riq_live_{'a1b2c3d4' * 4}"  # 32 hex chars
_VALID_PREFIX = _VALID_RAW_KEY[:_KEY_PREFIX_LEN]
_VALID_HASH = _PH.hash(_VALID_RAW_KEY)

# (id, org_id, name, key_hash, quota) — returned by the SELECT FOR UPDATE
_QUOTA = 1000
_SELECT_ROW = (_KEY_ID, _ORG_ID, "test-key", _VALID_HASH, _QUOTA)
# (count,) — returned by the monthly COUNT query
_COUNT_ZERO = (0,)
_COUNT_AT_QUOTA = (_QUOTA,)
# (id,) — returned by INSERT INTO usage_records ... RETURNING id
_USAGE_ROW = (_USAGE_ID,)


def _make_conn() -> tuple[MagicMock, MagicMock]:
    cur = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


# ---------------------------------------------------------------------------
# keygen
# ---------------------------------------------------------------------------


def test_generate_api_key_returns_three_values() -> None:
    raw, prefix, h = generate_api_key()
    assert isinstance(raw, str) and isinstance(prefix, str) and isinstance(h, str)


def test_generate_api_key_format() -> None:
    raw, _, _ = generate_api_key()
    assert raw.startswith("riq_live_")
    assert len(raw) == 9 + 32
    assert all(c in "0123456789abcdef" for c in raw[9:])


def test_generate_api_key_prefix_is_first_17_chars() -> None:
    raw, prefix, _ = generate_api_key()
    assert prefix == raw[:17]
    assert len(prefix) == 17


def test_generate_api_key_hash_verifies() -> None:
    raw, _, h = generate_api_key()
    assert _PH.verify(h, raw)


def test_generate_api_key_unique() -> None:
    keys = {generate_api_key()[0] for _ in range(50)}
    assert len(keys) == 50


# ---------------------------------------------------------------------------
# require_api_key — header / format validation (no DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_key_raises_401() -> None:
    with pytest.raises(HTTPException) as exc:
        await require_api_key(bearer=None, x_api_key=None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_wrong_prefix_raises_401() -> None:
    creds = HTTPAuthorizationCredentials(scheme="bearer", credentials="sk_live_" + "a" * 32)
    with pytest.raises(HTTPException) as exc:
        await require_api_key(bearer=creds, x_api_key=None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_short_hex_raises_401() -> None:
    creds = HTTPAuthorizationCredentials(scheme="bearer", credentials="riq_live_abc")
    with pytest.raises(HTTPException) as exc:
        await require_api_key(bearer=creds, x_api_key=None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_uppercase_hex_raises_401() -> None:
    creds = HTTPAuthorizationCredentials(scheme="bearer", credentials="riq_live_" + "A" * 32)
    with pytest.raises(HTTPException) as exc:
        await require_api_key(bearer=creds, x_api_key=None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_bearer_takes_precedence_over_x_api_key() -> None:
    other = "riq_live_" + "b" * 32
    creds = HTTPAuthorizationCredentials(scheme="bearer", credentials=_VALID_RAW_KEY)
    with patch("app.auth.api_key._lookup_and_record") as mock_lookup:
        mock_lookup.return_value = ApiKeyContext(
            org_id=str(_ORG_ID), api_key_id=str(_KEY_ID), key_name="k",
            usage_record_id=str(_USAGE_ID),
        )
        await require_api_key(bearer=creds, x_api_key=other)
    mock_lookup.assert_called_once_with(_VALID_RAW_KEY)


@pytest.mark.asyncio
async def test_x_api_key_accepted_without_bearer() -> None:
    with patch("app.auth.api_key._lookup_and_record") as mock_lookup:
        mock_lookup.return_value = ApiKeyContext(
            org_id=str(_ORG_ID), api_key_id=str(_KEY_ID), key_name="k",
            usage_record_id=str(_USAGE_ID),
        )
        await require_api_key(bearer=None, x_api_key=_VALID_RAW_KEY)
    mock_lookup.assert_called_once_with(_VALID_RAW_KEY)


# ---------------------------------------------------------------------------
# _lookup_and_record — DB logic (psycopg2 + _PH mocked)
#
# _PH is a C-extension PasswordHasher instance; patch the whole object.
# ---------------------------------------------------------------------------


def test_prefix_not_found_raises_401() -> None:
    conn, cur = _make_conn()
    cur.fetchone.return_value = None  # SELECT returns nothing

    with patch("app.auth.api_key._db_connect", return_value=conn):
        with pytest.raises(HTTPException) as exc:
            _lookup_and_record(_VALID_RAW_KEY)

    assert exc.value.status_code == 401
    conn.rollback.assert_called_once()
    conn.close.assert_called_once()


def test_argon2_mismatch_raises_401() -> None:
    conn, cur = _make_conn()
    cur.fetchone.return_value = _SELECT_ROW  # SELECT succeeds

    with patch("app.auth.api_key._db_connect", return_value=conn):
        with patch("app.auth.api_key._PH") as mock_ph:
            mock_ph.verify.side_effect = VerifyMismatchError()
            with pytest.raises(HTTPException) as exc:
                _lookup_and_record(_VALID_RAW_KEY)

    assert exc.value.status_code == 401
    conn.rollback.assert_called_once()


def test_quota_exceeded_raises_429() -> None:
    conn, cur = _make_conn()
    # SELECT ok, COUNT = quota (at limit)
    cur.fetchone.side_effect = [_SELECT_ROW, _COUNT_AT_QUOTA]

    with patch("app.auth.api_key._db_connect", return_value=conn):
        with patch("app.auth.api_key._PH") as mock_ph:
            mock_ph.verify.return_value = True
            with pytest.raises(HTTPException) as exc:
                _lookup_and_record(_VALID_RAW_KEY)

    assert exc.value.status_code == 429
    conn.rollback.assert_called_once()


def test_valid_key_returns_context() -> None:
    conn, cur = _make_conn()
    # SELECT ok, COUNT = 0 (well under quota), INSERT RETURNING id
    cur.fetchone.side_effect = [_SELECT_ROW, _COUNT_ZERO, _USAGE_ROW]

    with patch("app.auth.api_key._db_connect", return_value=conn):
        with patch("app.auth.api_key._PH") as mock_ph:
            mock_ph.verify.return_value = True
            ctx = _lookup_and_record(_VALID_RAW_KEY)

    assert ctx.org_id == str(_ORG_ID)
    assert ctx.api_key_id == str(_KEY_ID)
    assert ctx.key_name == "test-key"
    assert ctx.usage_record_id == str(_USAGE_ID)
    conn.commit.assert_called_once()
    conn.rollback.assert_not_called()


def test_execute_calls_in_correct_order() -> None:
    """SELECT FOR UPDATE → COUNT monthly usage → UPDATE last_used_at → INSERT usage_records."""
    conn, cur = _make_conn()
    cur.fetchone.side_effect = [_SELECT_ROW, _COUNT_ZERO, _USAGE_ROW]

    with patch("app.auth.api_key._db_connect", return_value=conn):
        with patch("app.auth.api_key._PH") as mock_ph:
            mock_ph.verify.return_value = True
            _lookup_and_record(_VALID_RAW_KEY)

    sqls = [c[0][0] for c in cur.execute.call_args_list]
    assert len(sqls) == 4
    assert "SELECT" in sqls[0] and "FOR UPDATE" in sqls[0]
    assert "COUNT" in sqls[1]
    assert "UPDATE" in sqls[2]
    assert "INSERT" in sqls[3]
    # Prefix is passed as parameter to the SELECT FOR UPDATE
    assert _VALID_PREFIX in cur.execute.call_args_list[0][0][1]


def test_db_error_triggers_rollback() -> None:
    conn, cur = _make_conn()
    cur.fetchone.side_effect = [_SELECT_ROW, _COUNT_ZERO, _USAGE_ROW]
    # SELECT, COUNT, UPDATE succeed; INSERT fails
    cur.execute.side_effect = [None, None, None, Exception("DB down")]

    with patch("app.auth.api_key._db_connect", return_value=conn):
        with patch("app.auth.api_key._PH") as mock_ph:
            mock_ph.verify.return_value = True
            with pytest.raises(Exception, match="DB down"):
                _lookup_and_record(_VALID_RAW_KEY)

    conn.rollback.assert_called_once()
    conn.close.assert_called_once()


def test_db_connect_uses_supabase_database_url() -> None:
    """_db_connect calls psycopg2.connect with the supabase_database_url from settings."""
    with patch("app.auth.api_key.get_settings") as mock_settings, \
         patch("app.auth.api_key.psycopg2") as mock_psycopg2:
        mock_settings.return_value.supabase_database_url = "postgresql://user:pw@host/db"
        mock_psycopg2.connect.return_value = MagicMock()
        from app.auth.api_key import _db_connect
        _db_connect()
    mock_psycopg2.connect.assert_called_once_with("postgresql://user:pw@host/db")
