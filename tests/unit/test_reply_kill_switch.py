"""ENABLE_REPLY_DRAFTING kill switch: default off, typed 503, zero provider calls when off."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from app.auth.api_key import ApiKeyContext, require_api_key
from app.auth.session import require_session
from app.core.config import Settings
from app.core.reply.schema import ReplyDraft, ReplyTone
from app.main import create_app

_CTX = ApiKeyContext(org_id="test-org", api_key_id="k", key_name="k", usage_record_id="u")
_BODY = {"text": "The stitching came apart after one wash.", "tone": "apologetic"}


def _set_flag(monkeypatch: pytest.MonkeyPatch, value: bool) -> None:
    monkeypatch.setattr(
        "app.api.v2.reply.get_settings", lambda: SimpleNamespace(enable_reply_drafting=value)
    )


@pytest.fixture(autouse=True)
def _clean_draft_cache() -> None:
    """The per-process draft caches are module globals; keep them from leaking across tests."""
    from app.api.bff import router as bff_router
    from app.api.v2 import reply as v2_reply

    bff_router._DRAFT_CACHE.clear()
    v2_reply._DRAFT_CACHE.clear()
    yield
    bff_router._DRAFT_CACHE.clear()
    v2_reply._DRAFT_CACHE.clear()


@pytest.fixture()
async def client() -> httpx.AsyncClient:
    app = create_app()
    app.dependency_overrides[require_session] = lambda: _CTX
    app.dependency_overrides[require_api_key] = lambda: _CTX
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


def test_flag_defaults_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_REPLY_DRAFTING", raising=False)
    assert Settings(_env_file=None).enable_reply_drafting is False


def test_flag_reads_env_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_REPLY_DRAFTING", "true")
    assert Settings(_env_file=None).enable_reply_drafting is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/bff/reply", _BODY),
        ("/v2/reply", _BODY),
        ("/v2/reply/batch", {"reviews": [_BODY]}),
    ],
)
async def test_disabled_returns_typed_503_and_no_provider_call(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    payload: dict,
) -> None:
    _set_flag(monkeypatch, False)
    drafter = AsyncMock()
    groq = MagicMock()
    monkeypatch.setattr("app.api.bff.router.draft_reply", drafter)
    monkeypatch.setattr("app.api.v2.reply.draft_reply", drafter)
    monkeypatch.setattr("app.core.reply.engine._call_groq", groq)
    monkeypatch.setattr("app.core.reply.engine.GroqProvider", groq)

    resp = await client.post(path, json=payload)

    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["code"] == "reply_drafting_disabled"
    assert "temporarily unavailable" in detail["message"]
    drafter.assert_not_called()
    groq.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_ignores_cached_drafts(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A draft cached while enabled must not be served after the switch is turned off."""
    draft = ReplyDraft(
        reply_text="cached",
        language="en",
        tone=ReplyTone.apologetic,
        grounded_on=[],
        caveats=[],
        model_used="m",
        drafted_at=datetime.now(UTC),
    )
    monkeypatch.setattr("app.api.bff.router.draft_reply", AsyncMock(return_value=(draft, 1, 1)))
    monkeypatch.setattr("app.api.bff.router.update_usage_tokens", AsyncMock())
    _set_flag(monkeypatch, True)
    assert (await client.post("/bff/reply", json=_BODY)).status_code == 200
    _set_flag(monkeypatch, False)
    assert (await client.post("/bff/reply", json=_BODY)).status_code == 503


@pytest.mark.asyncio
async def test_enabled_path_unchanged(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_flag(monkeypatch, True)
    draft = ReplyDraft(
        reply_text="Thanks for the feedback, sorry about the stitching.",
        language="en",
        tone=ReplyTone.apologetic,
        grounded_on=[],
        caveats=[],
        model_used="m",
        drafted_at=datetime.now(UTC),
    )
    drafter = AsyncMock(return_value=(draft, 3, 4))
    monkeypatch.setattr("app.api.bff.router.draft_reply", drafter)
    monkeypatch.setattr("app.api.bff.router.update_usage_tokens", AsyncMock())

    resp = await client.post("/bff/reply", json=_BODY)

    assert resp.status_code == 200
    assert resp.json()["reply_text"] == draft.reply_text
    drafter.assert_awaited_once()
