"""Per-user last_seen_at (S18 D3, ADR 0036): BFF endpoints + migration-file static checks.

Endpoint tests mock storage and the JWT verifier; the SQL behaviour (debounce, NULL for an
unknown user, no raise) is proven against a real Postgres in
tests/integration/test_last_seen_functions.py, which only CI's pre-cutover-verification job runs.
"""

from __future__ import annotations

import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.main import app
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase" / "migrations" / "20261007000001_organization_members_last_seen.sql"

sys.path.insert(0, str(ROOT / "supabase"))
import postconditions as pcmod  # noqa: E402

JWT_USER = str(uuid.uuid4())
OTHER_USER = str(uuid.uuid4())
BEARER = {"Authorization": "Bearer fake-jwt"}
STAMP = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)


def _user(uid: str) -> MagicMock:
    u = MagicMock()
    u.id = uid
    return u


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def jwt_ok():  # type: ignore[no-untyped-def]
    with patch(
        "app.auth.session.verify_supabase_jwt", new=AsyncMock(return_value=_user(JWT_USER))
    ) as m:
        yield m


# ---------------------------------------------------------------- GET


def test_get_returns_null_for_never_seen(client: TestClient, jwt_ok: AsyncMock) -> None:
    with patch("app.api.bff.router.get_last_seen_pg", return_value=None) as get:
        resp = client.get("/bff/account/last-seen", headers=BEARER)
    assert resp.status_code == 200
    assert resp.json() == {"last_seen_at": None}
    get.assert_called_once_with(JWT_USER)


def test_get_returns_iso_timestamp(client: TestClient, jwt_ok: AsyncMock) -> None:
    with patch("app.api.bff.router.get_last_seen_pg", return_value=STAMP):
        resp = client.get("/bff/account/last-seen", headers=BEARER)
    assert resp.status_code == 200
    assert datetime.fromisoformat(resp.json()["last_seen_at"]) == STAMP


def test_get_ignores_a_user_id_in_the_query(client: TestClient, jwt_ok: AsyncMock) -> None:
    with patch("app.api.bff.router.get_last_seen_pg", return_value=STAMP) as get:
        resp = client.get(
            f"/bff/account/last-seen?user_id={OTHER_USER}&userId={OTHER_USER}", headers=BEARER
        )
    assert resp.status_code == 200
    get.assert_called_once_with(JWT_USER)


def test_get_without_jwt_is_401_and_never_touches_storage(client: TestClient) -> None:
    with patch("app.api.bff.router.get_last_seen_pg") as get:
        resp = client.get("/bff/account/last-seen")
    assert resp.status_code == 401
    get.assert_not_called()


# ---------------------------------------------------------------- POST


def test_post_touches_and_returns_the_stored_value(client: TestClient, jwt_ok: AsyncMock) -> None:
    with patch("app.api.bff.router.touch_last_seen_pg", return_value=STAMP) as touch:
        resp = client.post("/bff/account/last-seen", headers=BEARER)
    assert resp.status_code == 200
    assert datetime.fromisoformat(resp.json()["last_seen_at"]) == STAMP
    touch.assert_called_once_with(JWT_USER)


def test_post_debounced_call_returns_the_existing_value(
    client: TestClient, jwt_ok: AsyncMock
) -> None:
    # The 60s debounce is enforced in SQL (touch_last_seen), which returns the stored value
    # unchanged when it is under 60s old. The endpoint must pass that value straight through,
    # not substitute "now" -- two calls in a burst therefore return the same timestamp.
    with patch("app.api.bff.router.touch_last_seen_pg", return_value=STAMP):
        first = client.post("/bff/account/last-seen", headers=BEARER).json()
        second = client.post("/bff/account/last-seen", headers=BEARER).json()
    assert first == second


def test_post_ignores_a_user_id_in_body_and_query(client: TestClient, jwt_ok: AsyncMock) -> None:
    with patch("app.api.bff.router.touch_last_seen_pg", return_value=STAMP) as touch:
        resp = client.post(
            f"/bff/account/last-seen?user_id={OTHER_USER}",
            headers=BEARER,
            json={"user_id": OTHER_USER, "userId": OTHER_USER},
        )
    assert resp.status_code == 200
    touch.assert_called_once_with(JWT_USER)


def test_post_without_jwt_is_401_and_never_touches_storage(client: TestClient) -> None:
    with patch("app.api.bff.router.touch_last_seen_pg") as touch:
        resp = client.post("/bff/account/last-seen")
    assert resp.status_code == 401
    touch.assert_not_called()


def test_post_with_invalid_jwt_is_401(client: TestClient) -> None:
    from fastapi import HTTPException

    with (
        patch(
            "app.auth.session.verify_supabase_jwt",
            new=AsyncMock(side_effect=HTTPException(status_code=401, detail="Invalid token.")),
        ),
        patch("app.api.bff.router.touch_last_seen_pg") as touch,
    ):
        resp = client.post("/bff/account/last-seen", headers=BEARER)
    assert resp.status_code == 401
    touch.assert_not_called()


def test_post_for_user_without_account_is_404(client: TestClient, jwt_ok: AsyncMock) -> None:
    with patch("app.api.bff.router.touch_last_seen_pg", return_value=None):
        resp = client.post("/bff/account/last-seen", headers=BEARER)
    assert resp.status_code == 404


def test_both_endpoints_are_documented_in_openapi() -> None:
    paths = app.openapi()["paths"]["/bff/account/last-seen"]
    for method in ("get", "post"):
        assert paths[method]["summary"]
        assert "JWT" in paths[method]["description"]
    for method in ("get", "post"):
        assert not paths[method].get("parameters"), "no user id may be a request parameter"
    assert "requestBody" not in paths["post"]


# ---------------------------------------------------------------- migration file (static)


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_postconditions_parse_and_are_unscoped() -> None:
    pcs = pcmod.parse_postconditions(_sql())
    assert {p.name for p in pcs} == {
        "organization_members_last_seen_column",
        "last_seen_functions_hardened",
        "organization_members_still_closed_to_anon_and_authenticated",
    }
    for pc in pcs:
        pcmod.validate_select(pc.sql)
        assert pc.scope is None


def test_migration_is_additive_only() -> None:
    body = "\n".join(line for line in _sql().splitlines() if not line.startswith("--"))
    assert "ADD COLUMN IF NOT EXISTS last_seen_at timestamptz;" in body
    # nullable, no default, no backfill
    assert not re.search(r"last_seen_at\s+timestamptz\s+(NOT NULL|DEFAULT)", body)
    assert not re.search(r"\b(DROP|TRUNCATE|DELETE)\b", body, re.IGNORECASE)


@pytest.mark.parametrize("fn", ["get_last_seen", "touch_last_seen"])
def test_migration_functions_follow_the_definer_pattern(fn: str) -> None:
    body = _sql()
    assert f"CREATE OR REPLACE FUNCTION public.{fn}(p_user_id uuid)" in body
    assert f"ALTER FUNCTION public.{fn}(uuid) OWNER TO review_iq_migrator;" in body
    assert (
        f"REVOKE ALL ON FUNCTION public.{fn}(uuid) FROM PUBLIC, anon, authenticated, service_role;"
        in body
    )
    assert f"GRANT EXECUTE ON FUNCTION public.{fn}(uuid) TO review_iq_app;" in body
    grants = re.findall(rf"GRANT EXECUTE ON FUNCTION public\.{fn}\(uuid\) TO (\w+);", body)
    assert grants == ["review_iq_app"]


def test_migration_functions_pin_search_path_and_are_security_definer() -> None:
    body = _sql()
    assert body.count("SECURITY DEFINER") >= 2
    assert body.count("SET search_path = public, pg_temp") == 2


def test_touch_is_debounced_in_sql_and_returns_null_without_raising() -> None:
    body = _sql()
    assert "last_seen_at IS NULL OR last_seen_at < now() - interval '60 seconds'" in body
    assert "RETURNING last_seen_at INTO v_seen" in body
    assert "RAISE" not in "\n".join(line for line in body.splitlines() if not line.startswith("--"))


def test_every_postcondition_of_this_migration_has_an_undo_case() -> None:
    undo = (ROOT / "tests" / "integration" / "test_push_postconditions.py").read_text(
        encoding="utf-8"
    )
    for pc in pcmod.parse_postconditions(_sql()):
        pattern = rf'"{MIGRATION.name}",\s*"{pc.name}",'
        assert re.search(pattern, undo), f"no UNDO_CASES entry for {pc.name}"
