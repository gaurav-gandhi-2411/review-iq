"""Unit tests for self-serve API key management (GET/POST/DELETE /bff/keys).

Covers both layers, matching test_admin_api.py's DB-helper-mocking style for the SQL
helpers and test_bff_session.py's dependency-override style for the HTTP routes.
The cross-org isolation test is the one this feature spec calls out explicitly: a caller
must never be able to revoke another org's key by guessing its UUID.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import httpx
import pytest
from app.api.bff.router import (
    PLAN_QUOTA_LIMITS,
    _create_key_bff_db,
    _get_org_plan_pg,
    _list_keys_bff_db,
    _revoke_key_bff_db,
)
from app.auth.api_key import ApiKeyContext
from app.auth.session import require_session, require_session_read
from fastapi import HTTPException
from pydantic import ValidationError

_ORG_A = str(uuid.uuid4())
_ORG_B = str(uuid.uuid4())
_KEY_ID = str(uuid.uuid4())
_NOW = datetime(2026, 7, 1, tzinfo=UTC)

_CTX_A = ApiKeyContext(
    org_id=_ORG_A, api_key_id=str(uuid.uuid4()), key_name="test-key-a", usage_record_id=""
)


def _make_conn() -> tuple[MagicMock, MagicMock]:
    cur = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


# ---------------------------------------------------------------------------
# DB helpers -- SQL-level tests (mirrors test_admin_api.py's style)
# ---------------------------------------------------------------------------


def test_create_key_bff_returns_raw_key() -> None:
    conn, cur = _make_conn()
    cur.fetchone.return_value = (uuid.UUID(_KEY_ID), _NOW)

    with patch("app.api.bff.router._keys_db_connect", return_value=conn):
        with patch(
            "app.api.bff.router.generate_api_key",
            return_value=("riq_live_" + "a" * 32, "riq_live_aaaaaaaa", "hash"),
        ):
            result = _create_key_bff_db(_ORG_A, "default", 1000)

    assert result["id"] == _KEY_ID
    assert result["raw_key"] == "riq_live_" + "a" * 32
    assert result["key_prefix"] == "riq_live_aaaaaaaa"
    assert result["quota"] == 1000
    conn.commit.assert_called_once()

    # org_id is bound as an INSERT parameter (scoped to the caller's own org). The INSERT
    # is the LAST cur.execute call now, not the first -- _set_tenant() (rule: RLS
    # defense-in-depth, see the section below) issues two SET LOCAL calls ahead of it.
    insert_call = cur.execute.call_args_list[-1]
    assert _ORG_A in insert_call[0][1]


def test_list_keys_bff_excludes_revoked_and_key_hash() -> None:
    conn, cur = _make_conn()
    cur.fetchall.return_value = [
        (uuid.UUID(_KEY_ID), "default", "riq_live_aabbccdd", 1000, _NOW),
    ]

    with patch("app.api.bff.router._keys_db_connect", return_value=conn):
        keys = _list_keys_bff_db(_ORG_A)

    assert len(keys) == 1
    assert keys[0]["key_prefix"] == "riq_live_aabbccdd"
    assert "key_hash" not in keys[0]
    assert "raw_key" not in keys[0]
    # WHERE clause filters to this org and excludes revoked keys.
    sql = cur.execute.call_args[0][0]
    assert "org_id = %s" in sql
    assert "revoked_at IS NULL" in sql


def test_revoke_key_bff_success() -> None:
    conn, cur = _make_conn()
    cur.fetchone.return_value = (uuid.UUID(_KEY_ID),)  # RETURNING id

    with patch("app.api.bff.router._keys_db_connect", return_value=conn):
        _revoke_key_bff_db(_ORG_A, _KEY_ID)  # must not raise

    conn.commit.assert_called_once()


def test_revoke_key_bff_cross_org_isolation() -> None:
    """A key belonging to org_B must not be revocable via org_A's context --
    the WHERE clause binds org_id = %s alongside id = %s, so a mismatched org_id
    means the UPDATE matches zero rows and the row is never touched."""
    conn, cur = _make_conn()
    cur.fetchone.return_value = None  # UPDATE ... RETURNING id matched nothing

    with patch("app.api.bff.router._keys_db_connect", return_value=conn):
        with pytest.raises(HTTPException) as exc:
            _revoke_key_bff_db(_ORG_A, _KEY_ID)  # org_A attempting org_B's key

    assert exc.value.status_code == 404
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()

    # The WHERE clause includes both id and org_id -- never id alone.
    sql, params = cur.execute.call_args[0]
    assert "id = %s" in sql
    assert "org_id = %s" in sql
    assert params == (_KEY_ID, _ORG_A)


def test_revoke_key_bff_not_found_generic_rolls_back() -> None:
    conn, cur = _make_conn()
    cur.execute.side_effect = Exception("connection reset")

    with patch("app.api.bff.router._keys_db_connect", return_value=conn):
        with pytest.raises(Exception, match="connection reset"):
            _revoke_key_bff_db(_ORG_A, _KEY_ID)

    conn.rollback.assert_called_once()
    conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# RLS defense-in-depth -- bug fix (found in code review after PR #45 landed
# ungated): these DB helpers connected via bare psycopg2.connect() and never
# called _set_tenant(), unlike every other function in app/core/storage_pg.py.
# The WHERE-clause scoping was already correct (no cross-tenant access was
# ever actually possible), but without _set_tenant() the connection ran
# outside the `authenticated` role RLS checks -- these confirm the second
# layer is now genuinely wired, not just present in a comment.
# ---------------------------------------------------------------------------


def test_create_key_bff_calls_set_tenant() -> None:
    conn, cur = _make_conn()
    cur.fetchone.return_value = (uuid.UUID(_KEY_ID), _NOW)

    with patch("app.api.bff.router._keys_db_connect", return_value=conn):
        with patch("app.api.bff.router.generate_api_key", return_value=("k", "p", "h")):
            with patch("app.api.bff.router._set_tenant") as mock_set_tenant:
                _create_key_bff_db(_ORG_A, "default", 1000)

    mock_set_tenant.assert_called_once_with(cur, _ORG_A)


def test_list_keys_bff_calls_set_tenant() -> None:
    conn, cur = _make_conn()
    cur.fetchall.return_value = []

    with patch("app.api.bff.router._keys_db_connect", return_value=conn):
        with patch("app.api.bff.router._set_tenant") as mock_set_tenant:
            _list_keys_bff_db(_ORG_A)

    mock_set_tenant.assert_called_once_with(cur, _ORG_A)


def test_revoke_key_bff_calls_set_tenant() -> None:
    conn, cur = _make_conn()
    cur.fetchone.return_value = (uuid.UUID(_KEY_ID),)

    with patch("app.api.bff.router._keys_db_connect", return_value=conn):
        with patch("app.api.bff.router._set_tenant") as mock_set_tenant:
            _revoke_key_bff_db(_ORG_A, _KEY_ID)

    mock_set_tenant.assert_called_once_with(cur, _ORG_A)


# ---------------------------------------------------------------------------
# Plan-entitlement quota bounding -- the actual incident fix. CreateApiKeyRequest.
# quota had no bound at all and was written verbatim to the column
# app/auth/api_key.py:99 uses as the sole gate on request admission; any
# signed-in free-tier user could self-issue a key with an arbitrarily large
# quota via POST /bff/keys.
# ---------------------------------------------------------------------------


def test_get_org_plan_bff_calls_set_tenant() -> None:
    conn, cur = _make_conn()
    cur.fetchone.return_value = ("pro",)

    with patch("app.api.bff.router._keys_db_connect", return_value=conn):
        with patch("app.api.bff.router._set_tenant") as mock_set_tenant:
            plan = _get_org_plan_pg(_ORG_A)

    mock_set_tenant.assert_called_once_with(cur, _ORG_A)
    assert plan == "pro"


def test_get_org_plan_bff_defaults_to_free_when_row_missing() -> None:
    """An org row that can't be found (should never happen for a real caller, but the
    query must not silently return an unbounded/None plan) fails closed to the
    strictest tier, not to no limit at all."""
    conn, cur = _make_conn()
    cur.fetchone.return_value = None

    with patch("app.api.bff.router._keys_db_connect", return_value=conn):
        plan = _get_org_plan_pg(_ORG_A)

    assert plan == "free"


def test_create_api_key_request_rejects_zero_quota() -> None:
    """quota=0 previously locked a key out permanently (monthly_count >= 0 is always
    true) with no validation error -- a second, independent bug the same Field(ge=1)
    fix closes."""
    from app.api.bff.router import CreateApiKeyRequest

    with pytest.raises(ValidationError):
        CreateApiKeyRequest(name="default", quota=0)


def test_create_api_key_request_rejects_negative_quota() -> None:
    from app.api.bff.router import CreateApiKeyRequest

    with pytest.raises(ValidationError):
        CreateApiKeyRequest(name="default", quota=-5)


# ---------------------------------------------------------------------------
# HTTP routes -- session-auth wiring tests
# ---------------------------------------------------------------------------


@pytest.fixture()
async def client() -> httpx.AsyncClient:
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[require_session] = lambda: _CTX_A
    app.dependency_overrides[require_session_read] = lambda: _CTX_A
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


async def test_list_keys_route_happy_path(client: httpx.AsyncClient) -> None:
    with patch(
        "app.api.bff.router._list_keys_bff_db",
        return_value=[
            {
                "id": _KEY_ID,
                "name": "default",
                "key_prefix": "riq_live_aabbccdd",
                "quota": 1000,
                "created_at": _NOW,
            }
        ],
    ):
        resp = await client.get("/bff/keys")

    assert resp.status_code == 200
    body = resp.json()
    assert body["org_id"] == _ORG_A
    assert body["keys"][0]["key_prefix"] == "riq_live_aabbccdd"
    assert "key_hash" not in resp.text
    assert "raw_key" not in resp.text


async def test_create_key_route_happy_path(client: httpx.AsyncClient) -> None:
    with patch("app.api.bff.router._get_org_plan_pg", return_value="pro"):
        with patch(
            "app.api.bff.router._create_key_bff_db",
            return_value={
                "id": _KEY_ID,
                "raw_key": "riq_live_" + "a" * 32,
                "key_prefix": "riq_live_aaaaaaaa",
                "name": "default",
                "quota": 1000,
                "created_at": _NOW,
            },
        ) as mock_create:
            resp = await client.post("/bff/keys", json={"name": "default", "quota": 1000})

    assert resp.status_code == 201
    body = resp.json()
    assert body["raw_key"] == "riq_live_" + "a" * 32
    assert body["key_prefix"] == "riq_live_aaaaaaaa"
    mock_create.assert_called_once_with(_ORG_A, "default", 1000)


async def test_create_key_route_rejects_over_limit_quota_for_free_plan(
    client: httpx.AsyncClient,
) -> None:
    """The actual incident: a free-tier org must not be able to self-issue a key with
    an arbitrarily large quota. quota=999999999 is well above PLAN_QUOTA_LIMITS['free']."""
    with patch("app.api.bff.router._get_org_plan_pg", return_value="free"):
        with patch("app.api.bff.router._create_key_bff_db") as mock_create:
            resp = await client.post("/bff/keys", json={"name": "default", "quota": 999999999})

    assert resp.status_code == 400
    assert "free" in resp.json()["detail"]
    assert str(PLAN_QUOTA_LIMITS["free"]) in resp.json()["detail"]
    mock_create.assert_not_called()  # rejected before any DB write


async def test_create_key_route_allows_quota_at_exact_free_plan_limit(
    client: httpx.AsyncClient,
) -> None:
    """A request for exactly the plan's limit is allowed -- the check is a hard
    ceiling (`>`), not an off-by-one that also rejects the boundary value itself."""
    with patch("app.api.bff.router._get_org_plan_pg", return_value="free"):
        with patch(
            "app.api.bff.router._create_key_bff_db",
            return_value={
                "id": _KEY_ID,
                "raw_key": "riq_live_" + "a" * 32,
                "key_prefix": "riq_live_aaaaaaaa",
                "name": "default",
                "quota": PLAN_QUOTA_LIMITS["free"],
                "created_at": _NOW,
            },
        ) as mock_create:
            resp = await client.post(
                "/bff/keys",
                json={"name": "default", "quota": PLAN_QUOTA_LIMITS["free"]},
            )

    assert resp.status_code == 201
    mock_create.assert_called_once_with(_ORG_A, "default", PLAN_QUOTA_LIMITS["free"])


async def test_create_key_route_pro_plan_allows_above_free_limit(
    client: httpx.AsyncClient,
) -> None:
    """A pro-plan org may request a quota above the free tier's limit, up to its own
    (higher) plan limit -- confirms the bound is genuinely plan-aware, not a single
    flat max applied to every caller regardless of tier."""
    requested = PLAN_QUOTA_LIMITS["free"] + 1
    assert requested <= PLAN_QUOTA_LIMITS["pro"]  # sanity: this case must be allowed
    with patch("app.api.bff.router._get_org_plan_pg", return_value="pro"):
        with patch(
            "app.api.bff.router._create_key_bff_db",
            return_value={
                "id": _KEY_ID,
                "raw_key": "riq_live_" + "a" * 32,
                "key_prefix": "riq_live_aaaaaaaa",
                "name": "default",
                "quota": requested,
                "created_at": _NOW,
            },
        ) as mock_create:
            resp = await client.post("/bff/keys", json={"name": "default", "quota": requested})

    assert resp.status_code == 201
    mock_create.assert_called_once_with(_ORG_A, "default", requested)


async def test_create_key_route_unrecognized_plan_falls_back_to_free_limit(
    client: httpx.AsyncClient,
) -> None:
    """A plan value not in PLAN_QUOTA_LIMITS (e.g. a future tier added to the DB CHECK
    constraint but not yet wired into this table) must fail closed to the strictest
    limit, never default to unlimited."""
    with patch("app.api.bff.router._get_org_plan_pg", return_value="some_future_tier"):
        with patch("app.api.bff.router._create_key_bff_db") as mock_create:
            resp = await client.post(
                "/bff/keys",
                json={"name": "default", "quota": PLAN_QUOTA_LIMITS["free"] + 1},
            )

    assert resp.status_code == 400
    mock_create.assert_not_called()


async def test_create_key_route_rejects_zero_quota(client: httpx.AsyncClient) -> None:
    resp = await client.post("/bff/keys", json={"name": "default", "quota": 0})
    assert resp.status_code == 422


async def test_revoke_key_route_scopes_to_callers_org(client: httpx.AsyncClient) -> None:
    """The route must call the DB helper with ctx.org_id, never a client-supplied org_id --
    there is no org_id in the request at all, only the path's key_id."""
    captured: list[tuple[str, str]] = []

    def _mock_revoke(org_id: str, key_id: str) -> None:
        captured.append((org_id, key_id))

    with patch("app.api.bff.router._revoke_key_bff_db", side_effect=_mock_revoke):
        resp = await client.delete(f"/bff/keys/{_KEY_ID}")

    assert resp.status_code == 204
    assert captured == [(_ORG_A, _KEY_ID)]
    assert _ORG_B not in str(captured)


async def test_revoke_key_route_not_found_returns_404(client: httpx.AsyncClient) -> None:
    with patch(
        "app.api.bff.router._revoke_key_bff_db",
        side_effect=HTTPException(status_code=404, detail="Key not found or already revoked."),
    ):
        resp = await client.delete(f"/bff/keys/{_KEY_ID}")

    assert resp.status_code == 404
