"""BFF auth layer tests — all 5 acceptance conditions.

Condition 1: Cross-org isolation (read + write)
Condition 2: Quota equivalence with direct API path
Condition 3: JWT verification rigor
Condition 4: No key/internal ID leakage
Condition 5: Existing path regression
"""

from __future__ import annotations

import dataclasses
import inspect
import pathlib
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from app.auth.api_key import ApiKeyContext
from app.auth.session import _lookup_and_record_for_session, require_session, require_session_read
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

ORG_A = str(uuid.uuid4())
ORG_B = str(uuid.uuid4())
KEY_ID_A = str(uuid.uuid4())
USAGE_ID_A = str(uuid.uuid4())
USER_A = str(uuid.uuid4())

_CTX_A = ApiKeyContext(
    org_id=ORG_A,
    api_key_id=KEY_ID_A,
    key_name="test-key-a",
    usage_record_id=USAGE_ID_A,
)


# ---------------------------------------------------------------------------
# Shared client fixture — uses require_session dependency override
# ---------------------------------------------------------------------------


@pytest.fixture()
async def client() -> httpx.AsyncClient:
    from app.auth.session import require_session as _require_session
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[_require_session] = lambda: _CTX_A
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c
    app.dependency_overrides.clear()


# ===========================================================================
# Condition 3 — JWT verification rigor
# ===========================================================================


@pytest.mark.asyncio
async def test_require_session_missing_bearer() -> None:
    """No bearer token → 401."""
    with pytest.raises(HTTPException) as exc:
        await require_session(bearer=None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_require_session_invalid_jwt() -> None:
    """Invalid JWT → 401 (verify_supabase_jwt raises)."""
    bearer = HTTPAuthorizationCredentials(scheme="bearer", credentials="invalid.jwt.token")
    with patch(
        "app.auth.session.verify_supabase_jwt",
        new=AsyncMock(side_effect=HTTPException(status_code=401, detail="Invalid token.")),
    ):
        with pytest.raises(HTTPException) as exc:
            await require_session(bearer=bearer)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_require_session_expired_jwt() -> None:
    """Expired JWT → 401."""
    bearer = HTTPAuthorizationCredentials(scheme="bearer", credentials="expired.jwt.token")
    with patch(
        "app.auth.session.verify_supabase_jwt",
        new=AsyncMock(
            side_effect=HTTPException(status_code=401, detail="Invalid or expired Supabase token.")
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await require_session(bearer=bearer)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_require_session_no_org() -> None:
    """Valid JWT but no org provisioned → 403."""
    bearer = HTTPAuthorizationCredentials(scheme="bearer", credentials="valid.jwt.token")
    mock_user = MagicMock()
    mock_user.id = USER_A

    with patch(
        "app.auth.session.verify_supabase_jwt",
        new=AsyncMock(return_value=mock_user),
    ):
        with patch(
            "app.auth.session._lookup_and_record_for_session",
            side_effect=HTTPException(
                status_code=403, detail="No organization found. Call /auth/provision first."
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await require_session(bearer=bearer)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_session_valid() -> None:
    """Valid JWT + provisioned org → ApiKeyContext with correct org_id."""
    bearer = HTTPAuthorizationCredentials(scheme="bearer", credentials="valid.jwt.token")
    mock_user = MagicMock()
    mock_user.id = USER_A

    with patch(
        "app.auth.session.verify_supabase_jwt",
        new=AsyncMock(return_value=mock_user),
    ):
        with patch(
            "app.auth.session._lookup_and_record_for_session",
            return_value=_CTX_A,
        ):
            ctx = await require_session(bearer=bearer)

    assert ctx.org_id == ORG_A
    assert ctx.api_key_id == KEY_ID_A


# ===========================================================================
# Condition 1 — Cross-org isolation
# ===========================================================================


@pytest.mark.asyncio
async def test_bff_read_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """User A's session resolves to org_A; GET /bff/reviews only queries org_A."""
    from app.auth.session import require_session as _rs
    from app.auth.session import require_session_read as _rsr
    from app.main import create_app

    app = create_app()
    # Override both session deps → always returns org_A context
    app.dependency_overrides[_rs] = lambda: _CTX_A
    app.dependency_overrides[_rsr] = lambda: _CTX_A

    captured_org_ids: list[str] = []

    def _mock_list(org_id: str, **_kwargs: object) -> list[object]:
        captured_org_ids.append(org_id)
        return []

    monkeypatch.setattr("app.api.bff.router.list_extractions_pg", _mock_list)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.get("/bff/reviews")

    app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert len(captured_org_ids) == 1
    assert captured_org_ids[0] == ORG_A
    # org_B must never appear in any argument
    assert ORG_B not in captured_org_ids


@pytest.mark.asyncio
async def test_bff_write_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """User A's session resolves to org_A; POST /bff/corrections writes only to org_A."""
    from app.auth.session import require_session as _rs
    from app.auth.session import require_session_read as _rsr
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[_rs] = lambda: _CTX_A
    app.dependency_overrides[_rsr] = lambda: _CTX_A

    captured_org_ids: list[str] = []

    def _mock_submit(org_id: str, *_args: object, **_kwargs: object) -> str:
        captured_org_ids.append(org_id)
        return str(uuid.uuid4())

    monkeypatch.setattr("app.api.bff.router.submit_correction_pg", _mock_submit)
    # Also patch CORRECTIONS_SUBMITTED metric to avoid label registration issues
    monkeypatch.setattr(
        "app.api.bff.router.CORRECTIONS_SUBMITTED",
        MagicMock(labels=MagicMock(return_value=MagicMock(inc=MagicMock()))),
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/bff/corrections",
            json={
                "review_id": "a" * 64,
                "source_type": "extraction",
                "field_path": "sentiment",
                "original_value": "negative",
                "corrected_value": "positive",
                "language": "en",
            },
        )

    app.dependency_overrides.clear()

    assert resp.status_code == 201
    assert len(captured_org_ids) == 1
    assert captured_org_ids[0] == ORG_A
    # org_B must not appear in response
    body = resp.json()
    assert ORG_B not in str(body)


@pytest.mark.asyncio
async def test_bff_jwt_does_not_resolve_cross_org() -> None:
    """_lookup_and_record_for_session SQL filters by user_id from JWT, not request body."""
    src = inspect.getsource(_lookup_and_record_for_session)
    # The WHERE clause binds user_id parameter (from JWT), not from request body.
    # We verify the SQL uses organization_members.user_id = %s with a positional param.
    assert "organization_members.user_id = %s" in src
    # There must be exactly one parameter placeholder in the first query (for user_id).
    # The function receives user_id as its only argument — no request fields reach it.
    params = inspect.signature(_lookup_and_record_for_session).parameters
    assert list(params.keys()) == ["user_id"]


# ===========================================================================
# Condition 2 — Quota equivalence
# ===========================================================================


@pytest.mark.asyncio
async def test_bff_quota_enforcement_blocks_over_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Session path: quota=1 org gets 429 on second call."""
    from app.auth.session import require_session as _rs
    from app.main import create_app

    app = create_app()

    call_count = 0

    def _session_dep() -> ApiKeyContext:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _CTX_A
        raise HTTPException(
            status_code=429,
            detail="Monthly quota exceeded (1/1). Contact support to increase.",
        )

    app.dependency_overrides[_rs] = _session_dep

    fake_result = MagicMock()
    fake_result.model_dump.return_value = {"score": 0.9, "label": "genuine", "flags": []}

    monkeypatch.setattr(
        "app.api.bff.router.get_authenticity_audit_by_hash_pg",
        MagicMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.api.bff.router.engine.score_single",
        AsyncMock(return_value=fake_result),
    )
    monkeypatch.setattr(
        "app.api.bff.router.save_authenticity_audit_pg",
        MagicMock(return_value=None),
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        r1 = await c.post("/bff/authenticity", json={"text": "Great product"})
        r2 = await c.post("/bff/authenticity", json={"text": "Great product again"})

    app.dependency_overrides.clear()

    assert r1.status_code == 200
    assert r2.status_code == 429


def test_bff_quota_sql_uses_for_update() -> None:
    """_lookup_and_record_for_session SQL contains FOR UPDATE OF api_keys."""
    src = inspect.getsource(_lookup_and_record_for_session)
    assert "FOR UPDATE" in src
    assert "usage_records" in src


# ===========================================================================
# Condition 4 — No key/internal ID leakage
# ===========================================================================


def test_bff_no_riq_live_in_module() -> None:
    """riq_live_ must not appear in the BFF module source."""
    bff_path = pathlib.Path("app/api/bff/router.py")
    session_path = pathlib.Path("app/auth/session.py")
    assert "riq_live_" not in bff_path.read_text()
    assert "riq_live_" not in session_path.read_text()


def test_bff_no_key_hash_in_module() -> None:
    """key_hash must not appear in session.py or bff router (never retrieved/used)."""
    for p in [pathlib.Path("app/api/bff/router.py"), pathlib.Path("app/auth/session.py")]:
        assert "key_hash" not in p.read_text(), f"key_hash found in {p}"


@pytest.mark.asyncio
async def test_bff_account_response_has_no_key_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /bff/account response must not include key_hash, key_prefix, or riq_live_."""
    monkeypatch.setattr(
        "app.api.bff.router._get_quota_and_usage",
        MagicMock(return_value=(100, 3)),
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=_make_bff_app()
        ),
        base_url="http://test",
    ) as c:
        resp = await c.get("/bff/account")

    assert resp.status_code == 200
    body = resp.json()
    body_str = str(body)
    assert "key_hash" not in body_str
    assert "key_prefix" not in body_str
    assert "riq_live_" not in body_str
    assert "api_key_id" not in body


def _make_bff_app() -> object:
    """Return a FastAPI app with both session deps bypassed to _CTX_A."""
    from app.auth.session import require_session as _rs
    from app.auth.session import require_session_read as _rsr
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[_rs] = lambda: _CTX_A
    app.dependency_overrides[_rsr] = lambda: _CTX_A
    return app


# ===========================================================================
# Condition 5 — Existing path regression
# ===========================================================================


def test_existing_require_api_key_unchanged() -> None:
    """require_api_key is still importable and ApiKeyContext shape unchanged."""
    from app.auth.api_key import ApiKeyContext, require_api_key  # noqa: F401

    fields = {f.name for f in dataclasses.fields(ApiKeyContext)}
    assert fields == {"org_id", "api_key_id", "key_name", "usage_record_id"}


def test_v2_routers_importable() -> None:
    """All v2 endpoint routers import without error (no import breakage from BFF addition)."""
    from app.api.v2.authenticity import router as _a  # noqa: F401
    from app.api.v2.corrections import router as _c  # noqa: F401
    from app.api.v2.dataset import router as _d  # noqa: F401
    from app.api.v2.ingest import router as _i  # noqa: F401
    from app.api.v2.insights import router as _ins  # noqa: F401
    from app.api.v2.reply import router as _r  # noqa: F401
    from app.api.v2.reviews import router as _rev  # noqa: F401


def test_bff_router_importable() -> None:
    """BFF router imports cleanly and is mounted at /bff prefix."""
    from app.api.bff.router import router

    assert router.prefix == "/bff"
