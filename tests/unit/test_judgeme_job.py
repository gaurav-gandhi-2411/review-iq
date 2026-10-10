"""Scheduled-job policy tests (M5 failure handling, M7 retention, sweep + internal endpoint).

Fake Judge.me + in-memory pipeline; storage functions patched. Nothing touches a network or DB.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from app.api.webhooks.shopify import encrypt_token
from app.core.config import get_settings
from app.core.ingestion import judgeme_job as job
from app.core.ingestion.judgeme_client import JudgeMeClient
from cryptography.fernet import Fernet

from tests.support.judgeme_fake import EPOCH, SHOP, TOKEN, FakeJudgeMe, make_review
from tests.support.judgeme_memory import MemPipeline

ORG = str(uuid.uuid4())
INST = str(uuid.uuid4())
KEY = Fernet.generate_key().decode()
NOW = EPOCH + timedelta(days=2)


class Env:
    def __init__(self) -> None:
        self.server = FakeJudgeMe([make_review(i) for i in range(1, 6)])
        self.pipe = MemPipeline()
        self.inst: dict[str, Any] = {
            "id": INST,
            "shop_domain": SHOP,
            "auth_transport": "header",
            "token_enc": encrypt_token(TOKEN, KEY),
            "revoked_at": None,
            "watermark": None,
            "last_full_scan_at": None,
            "order_violation_at": None,
        }
        self.mode = "retained"
        self.mode_error = False
        self.records: list[dict[str, Any]] = []
        self.revoked: list[tuple] = []
        self.state_deleted = 0
        self.clients_built = 0


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch) -> Env:
    e = Env()

    async def nosleep(_: float) -> None:
        return None

    def make_client(shop: str, token: str, transport: Any) -> JudgeMeClient:
        e.clients_built += 1
        return JudgeMeClient(
            shop,
            token,
            auth_transport=transport,
            transport=e.server.transport(),
            sleep=nosleep,
            rng=lambda: 0.0,
        )

    def record(inst_id: str, org: str, **kw: Any) -> None:
        e.records.append(kw)
        if kw["watermark"]:
            e.inst["watermark"] = kw["watermark"]
        if kw["last_full_scan_at"]:
            e.inst["last_full_scan_at"] = kw["last_full_scan_at"]
        if kw["order_violation"]:
            e.inst["order_violation_at"] = NOW

    def retention(org: str) -> tuple[str, int | None]:
        if e.mode_error:
            raise RuntimeError("db down")
        return e.mode, 30 if e.mode == "retained" else None

    s = get_settings().model_copy(
        update={"enable_judgeme_connector": True, "judgeme_token_encryption_key": KEY}
    )
    monkeypatch.setattr(job, "get_settings", lambda: s)
    monkeypatch.setattr(job, "make_client", make_client)
    monkeypatch.setattr(job, "get_installation_pg", lambda *a, **k: dict(e.inst))
    monkeypatch.setattr(job, "get_org_retention_pg", retention)
    monkeypatch.setattr(job, "record_sync_pg", record)
    monkeypatch.setattr(job, "revoke_installation_pg", lambda *a: e.revoked.append(a))
    monkeypatch.setattr(
        job, "delete_state_pg", lambda *a: setattr(e, "state_deleted", e.state_deleted + 1)
    )
    monkeypatch.setattr(job, "PgBackend", lambda org, inst: e.pipe)
    return e


async def test_first_sync_ingests_everything_and_records_ok(env: Env) -> None:
    out = await job.sync_installation(INST, ORG, NOW)
    env.pipe.drain()
    assert out["installation"] == "ok" and out["new"] == 5 and out["mode"] == "full"
    assert len(env.pipe.extractions) == 5
    rec = env.records[0]
    assert (
        rec["status"] == "ok" and rec["watermark"] is not None and rec["last_full_scan_at"] == NOW
    )
    assert TOKEN not in repr(env.records)


async def test_five_consecutive_runs_stage_each_review_once(env: Env) -> None:
    for k in range(5):
        await job.sync_installation(INST, ORG, NOW + timedelta(hours=6 * k))
    ids = env.pipe.staged_ids()
    assert sorted(ids) == ["1", "2", "3", "4", "5"] and len(env.pipe.staged_log) == 1  # M2


async def test_stateless_org_does_nothing_at_all(env: Env) -> None:  # M7
    env.mode = "stateless"
    out = await job.sync_installation(INST, ORG, NOW)
    assert out == {"installation": "skipped", "reason": "stateless"}
    assert env.clients_built == 0 and env.server.request_log == [] and env.pipe.writes == 0
    assert env.state_deleted == 1 and env.records[0]["status"] == "skipped_stateless"


async def test_unreadable_retention_mode_fails_closed(env: Env) -> None:
    env.mode_error = True
    out = await job.sync_installation(INST, ORG, NOW)
    assert out["code"] == "retention_lookup_failed" and env.server.request_log == []
    assert env.records[0]["failed"] is True


async def test_bad_token_is_confirmed_once_then_installation_revoked(env: Env) -> None:
    env.server.token = "someone-else"
    out = await job.sync_installation(INST, ORG, NOW)
    assert out == {"installation": "revoked", "reason": "token_rejected"}
    assert len(env.server.request_log) == 2  # one re-issue, then stop: never retried forever
    assert env.revoked == [(INST, ORG, "token_rejected")] and env.pipe.writes == 0
    env.inst["revoked_at"] = NOW  # status endpoint would now say needs_action=reconnect
    again = await job.sync_installation(INST, ORG, NOW + timedelta(hours=6))
    assert again["installation"] == "skipped" and len(env.server.request_log) == 2


async def test_token_revoked_after_successful_syncs(env: Env) -> None:
    await job.sync_installation(INST, ORG, NOW)
    env.server.revoke_token()
    out = await job.sync_installation(INST, ORG, NOW + timedelta(hours=6))
    assert out["installation"] == "revoked" and env.revoked[-1][2] == "token_rejected"
    assert len(env.pipe.state) == 5  # nothing was removed because the token was rejected


async def test_unknown_shop_revokes_with_its_own_reason(env: Env) -> None:
    env.server.shop = "other.myshopify.com"
    out = await job.sync_installation(INST, ORG, NOW)
    assert out["reason"] == "shop_not_found" and env.revoked[0][2] == "shop_not_found"


async def test_429_over_cap_defers_without_failure_or_writes(env: Env) -> None:
    env.server.fail_next(429, times=99, headers={"Retry-After": "3600"})
    out = await job.sync_installation(INST, ORG, NOW)
    rec = env.records[0]
    assert out["installation"] == "deferred" and rec["status"] == "rate_limited"
    assert rec["next_attempt_at"] == NOW + timedelta(hours=1) and rec["failed"] is False
    assert env.pipe.writes == 0 and env.revoked == []


async def test_429_without_header_exhausted_defers_default_window(env: Env) -> None:
    env.server.fail_next(429, times=99)
    await job.sync_installation(INST, ORG, NOW)
    assert env.records[0]["next_attempt_at"] == NOW + job.DEFAULT_RATE_LIMIT_DEFER


@pytest.mark.parametrize(
    ("arrange", "code"),
    [
        (lambda s: s.fail_next(502, times=99), "judgeme_unreachable"),
        (lambda s: s.timeout_next(times=99), "judgeme_unreachable"),
        (lambda s: s.malformed_next(times=99), "judgeme_malformed_response"),
    ],
)
async def test_transient_failures_are_recorded_not_revoked(
    env: Env, arrange: Any, code: str
) -> None:
    arrange(env.server)
    out = await job.sync_installation(INST, ORG, NOW)
    assert out == {"installation": "error", "code": code}
    assert env.records[0]["failed"] is True and env.revoked == [] and env.pipe.writes == 0


async def test_run_timeout_is_recorded_and_safe(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow(*a: Any, **k: Any) -> Any:
        await asyncio.sleep(5)

    monkeypatch.setattr(job, "_run_confirming_auth", slow)
    monkeypatch.setattr(job, "PER_INSTALLATION_TIMEOUT_S", 0.05)
    out = await job.sync_installation(INST, ORG, NOW)
    assert out["code"] == "sync_timeout" and env.records[0]["failed"] is True


async def test_unexpected_error_never_escapes(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("bug")

    monkeypatch.setattr(job, "run_sync", boom)
    out = await job.sync_installation(INST, ORG, NOW)
    assert out["code"] == "internal_error"


async def test_undecryptable_token_is_an_error_not_a_revoke(env: Env) -> None:
    env.inst["token_enc"] = encrypt_token(TOKEN, Fernet.generate_key().decode())  # rotated away
    out = await job.sync_installation(INST, ORG, NOW)
    assert (
        out["code"] == "token_undecryptable" and env.revoked == [] and env.server.request_log == []
    )


# ---- sweep and internal endpoint ----------------------------------------------------------------


async def test_sweep_flag_off_does_no_io(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    off = get_settings().model_copy(update={"enable_judgeme_connector": False})
    monkeypatch.setattr(job, "get_settings", lambda: off)
    monkeypatch.setattr(job, "list_due_installations_pg", lambda *_: pytest.fail("must not query"))
    assert await job.run_due_installations(NOW) == {"enabled": False}


async def test_sweep_isolates_failures_and_caps_batch(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = [(str(uuid.uuid4()), ORG) for _ in range(12)]
    monkeypatch.setattr(job, "list_due_installations_pg", lambda *_: ids)
    results = iter(
        [{"installation": "error"}, {"installation": "revoked"}] + [{"installation": "ok"}] * 20
    )

    async def fake_sync(*a: Any, **k: Any) -> dict[str, Any]:
        return next(results)

    monkeypatch.setattr(job, "sync_installation", fake_sync)
    out = await job.run_due_installations(NOW)
    assert out == {"enabled": True, "due": 12, "ran": 10, "revoked": 1, "errors": 1}


@pytest.fixture()
async def internal_client(monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.api.internal import judgeme as route
    from app.main import app

    s = get_settings().model_copy(update={"judgeme_sync_trigger_token": "trig-secret"})
    monkeypatch.setattr(route, "get_settings", lambda: s)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c, route, monkeypatch


async def test_internal_endpoint_auth_then_flag(internal_client: Any) -> None:
    c, route, mp = internal_client

    async def fake_run() -> dict[str, Any]:
        return {"enabled": False}

    mp.setattr(route, "run_due_installations", fake_run)
    assert (await c.post("/internal/judgeme/sync")).status_code == 401
    assert (
        await c.post("/internal/judgeme/sync", headers={"X-Judgeme-Sync-Token": "nope"})
    ).status_code == 401
    r = await c.post("/internal/judgeme/sync", headers={"X-Judgeme-Sync-Token": "trig-secret"})
    assert r.status_code == 200 and r.json() == {"enabled": False}


async def test_internal_endpoint_unconfigured_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.internal import judgeme as route
    from app.main import app

    s = get_settings().model_copy(update={"judgeme_sync_trigger_token": ""})
    monkeypatch.setattr(route, "get_settings", lambda: s)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/internal/judgeme/sync", headers={"X-Judgeme-Sync-Token": "x"})
    assert r.status_code == 503
