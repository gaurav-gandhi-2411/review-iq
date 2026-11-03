"""BFF /bff/judgeme/* tests: flag-off 404, SSRF, retention, probe outcomes, token handling.

Fake Judge.me server + patched storage; no network, no database.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from app.api.bff import judgeme as ep
from app.api.webhooks.shopify import _decrypt_token
from app.auth.api_key import ApiKeyContext
from app.auth.session import require_session, require_session_read
from app.core.config import Settings, get_settings
from app.core.ingestion.judgeme_client import JudgeMeClient
from app.core.ingestion.judgeme_store import ShopConnectedElsewhere
from cryptography.fernet import Fernet

from tests.support.judgeme_fake import SHOP, TOKEN, FakeJudgeMe, make_review

ORG = str(uuid.uuid4())
KEY = Fernet.generate_key().decode()


def ctx(mode: str = "retained") -> ApiKeyContext:
    return ApiKeyContext(
        org_id=ORG,
        api_key_id=str(uuid.uuid4()),
        key_name="t",
        usage_record_id="",
        retention_mode=mode,
        retention_days=30 if mode == "retained" else None,
    )


class Env:
    def __init__(self) -> None:
        self.server = FakeJudgeMe([make_review(1)])
        self.clients_built = 0
        self.upserts: list[tuple] = []
        self.revoked: list[tuple] = []
        self.state_deleted: list[tuple] = []
        self.installation: dict[str, Any] | None = None
        self.elsewhere = False


@pytest.fixture()
async def env(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[httpx.AsyncClient, Env]]:
    from app.main import app

    e = Env()

    def fake_client(shop: str, token: str, transport: str) -> JudgeMeClient:
        e.clients_built += 1

        async def nosleep(_: float) -> None:
            return None

        return JudgeMeClient(
            shop,
            token,
            auth_transport=transport,
            per_page=1,  # type: ignore[arg-type]
            transport=e.server.transport(),
            sleep=nosleep,
            rng=lambda: 0.0,
        )

    def upsert(org: str, shop: str, enc: str, transport: str) -> str:
        if e.elsewhere:
            raise ShopConnectedElsewhere(shop)
        e.upserts.append((org, shop, enc, transport))
        return "inst"

    s = get_settings().model_copy(
        update={"enable_judgeme_connector": True, "judgeme_token_encryption_key": KEY}
    )
    monkeypatch.setattr(ep, "get_settings", lambda: s)
    monkeypatch.setattr(ep, "make_client", fake_client)
    monkeypatch.setattr(ep, "upsert_installation_pg", upsert)
    monkeypatch.setattr(ep, "get_installation_pg", lambda org, *a, **k: e.installation)
    monkeypatch.setattr(ep, "count_tracked_pg", lambda *a: 7)
    monkeypatch.setattr(ep, "revoke_installation_pg", lambda *a: e.revoked.append(a))
    monkeypatch.setattr(ep, "delete_state_pg", lambda *a: e.state_deleted.append(a))
    app.dependency_overrides[require_session] = lambda: ctx()
    app.dependency_overrides[require_session_read] = lambda: ctx()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c, e
    app.dependency_overrides.clear()


def body(shop: str = SHOP, token: str = TOKEN) -> dict[str, str]:
    return {"shop_domain": shop, "api_token": token}


async def test_flag_off_is_404_everywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import app

    assert Settings().enable_judgeme_connector is False  # default
    off = get_settings().model_copy(update={"enable_judgeme_connector": False})
    monkeypatch.setattr(ep, "get_settings", lambda: off)
    app.dependency_overrides[require_session] = lambda: ctx()
    app.dependency_overrides[require_session_read] = lambda: ctx()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.post("/bff/judgeme/connect", json=body())).status_code == 404
        assert (await c.get("/bff/judgeme/status")).status_code == 404
        assert (await c.delete("/bff/judgeme/connection")).status_code == 404
    app.dependency_overrides.clear()


async def test_connect_happy_path_encrypts_and_never_echoes_token(env, caplog) -> None:  # type: ignore[no-untyped-def]
    c, e = env
    caplog.set_level(logging.DEBUG)
    r = await c.post("/bff/judgeme/connect", json=body())
    assert r.status_code == 201 and r.json() == {"status": "connected", "shop_domain": SHOP}
    org, shop, enc, transport = e.upserts[0]
    assert (org, shop, transport) == (ORG, SHOP, "header")
    assert enc != TOKEN and TOKEN not in enc and _decrypt_token(enc, KEY) == TOKEN
    assert TOKEN not in r.text and TOKEN not in caplog.text


async def test_connect_falls_back_to_query_transport_and_records_it(env) -> None:  # type: ignore[no-untyped-def]
    c, e = env
    e.server.accept_header_token = False  # header BELIEVED (F14) turns out unsupported
    r = await c.post("/bff/judgeme/connect", json=body())
    assert r.status_code == 201 and e.upserts[0][3] == "query"


@pytest.mark.parametrize(
    "bad",
    ["evil.com", "a.myshopify.com.evil.com", "x\n.myshopify.com", "", "http://a.myshopify.com"],
)
async def test_connect_rejects_hostile_shop_without_any_outbound_call(env, bad) -> None:  # type: ignore[no-untyped-def]
    c, e = env
    r = await c.post("/bff/judgeme/connect", json=body(shop=bad))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_shop_domain"
    assert e.clients_built == 0 and e.server.request_log == [] and e.upserts == []


async def test_connect_stateless_org_is_refused_before_probe(env) -> None:  # type: ignore[no-untyped-def]
    from app.main import app

    c, e = env
    app.dependency_overrides[require_session] = lambda: ctx("stateless")
    r = await c.post("/bff/judgeme/connect", json=body())
    assert r.status_code == 409 and r.json()["detail"]["code"] == "retention_required"
    assert e.clients_built == 0 and e.upserts == []


async def test_connect_bad_token_is_422_and_stores_nothing(env, caplog) -> None:  # type: ignore[no-untyped-def]
    c, e = env
    caplog.set_level(logging.DEBUG)
    r = await c.post("/bff/judgeme/connect", json=body(token="wrong-SECRET-token-123"))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "judgeme_bad_token"
    assert e.upserts == [] and e.clients_built == 2  # header, then query fallback
    assert "wrong-SECRET-token-123" not in r.text + caplog.text


async def test_connect_short_token_error_does_not_echo_it(env) -> None:  # type: ignore[no-untyped-def]
    c, _ = env
    r = await c.post("/bff/judgeme/connect", json=body(token="abc"))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "judgeme_bad_token"
    assert "abc" not in r.text


@pytest.mark.parametrize(
    ("arrange", "status", "code"),
    [
        (
            lambda s: s.fail_next(429, times=99, headers={"Retry-After": "9999"}),
            429,
            "judgeme_rate_limited",
        ),
        (lambda s: s.fail_next(503, times=99), 502, "judgeme_unreachable"),
        (lambda s: s.malformed_next(times=99), 502, "judgeme_malformed_response"),
        (lambda s: setattr(s, "shop", "other.myshopify.com"), 422, "judgeme_shop_not_found"),
    ],
)
async def test_connect_probe_failures_are_typed(env, arrange, status, code) -> None:  # type: ignore[no-untyped-def]
    c, e = env
    arrange(e.server)
    r = await c.post("/bff/judgeme/connect", json=body())
    assert r.status_code == status and r.json()["detail"]["code"] == code and e.upserts == []


async def test_connect_shop_owned_by_another_org_is_409(env) -> None:  # type: ignore[no-untyped-def]
    c, e = env
    e.elsewhere = True
    r = await c.post("/bff/judgeme/connect", json=body())
    assert r.status_code == 409 and r.json()["detail"]["code"] == "shop_connected_elsewhere"


async def test_connect_without_encryption_key_is_503(env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    c, e = env
    no_key = get_settings().model_copy(
        update={"enable_judgeme_connector": True, "judgeme_token_encryption_key": ""}
    )
    monkeypatch.setattr(ep, "get_settings", lambda: no_key)
    r = await c.post("/bff/judgeme/connect", json=body())
    assert r.status_code == 503 and e.clients_built == 0


def installation(**kw: Any) -> dict[str, Any]:
    base = {
        "id": uuid.uuid4(),
        "shop_domain": SHOP,
        "revoked_at": None,
        "revoked_reason": None,
        "last_sync_at": datetime(2026, 10, 1, tzinfo=UTC),
        "last_sync_status": "ok",
        "last_sync_error": None,
    }
    return base | kw


async def test_status_shapes(env) -> None:  # type: ignore[no-untyped-def]
    from app.main import app

    c, e = env
    r = await c.get("/bff/judgeme/status")
    assert r.json()["status"] == "never_connected" and r.json()["needs_action"] is None
    e.installation = installation()
    j = (await c.get("/bff/judgeme/status")).json()
    assert j["status"] == "active" and j["reviews_tracked"] == 7 and j["shop_domain"] == SHOP
    assert set(j) == {
        "status",
        "shop_domain",
        "last_sync_at",
        "last_sync_status",
        "last_error_code",
        "reviews_tracked",
        "needs_action",
    }  # no token, no ids
    e.installation = installation(revoked_at=datetime.now(UTC), revoked_reason="token_rejected")
    j = (await c.get("/bff/judgeme/status")).json()
    assert j["status"] == "revoked" and j["needs_action"] == "reconnect"
    e.installation = installation(revoked_at=datetime.now(UTC), revoked_reason="disconnected")
    assert (await c.get("/bff/judgeme/status")).json()["status"] == "disconnected"
    e.installation = installation()
    app.dependency_overrides[require_session_read] = lambda: ctx("stateless")
    j = (await c.get("/bff/judgeme/status")).json()
    assert j["status"] == "paused_stateless" and j["needs_action"] == "change_retention_mode"


async def test_disconnect_wipes_and_is_404_when_absent(env) -> None:  # type: ignore[no-untyped-def]
    c, e = env
    assert (await c.delete("/bff/judgeme/connection")).status_code == 404
    e.installation = installation()
    r = await c.delete("/bff/judgeme/connection")
    assert r.status_code == 204
    assert e.revoked[0][1:] == (ORG, "disconnected") and e.state_deleted[0][0] == ORG
