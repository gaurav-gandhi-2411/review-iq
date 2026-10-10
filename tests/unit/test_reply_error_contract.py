"""Q3c regressions: typed reply errors, CORS-exposed Retry-After, indexed batch failures."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import groq
import httpx
import pytest
from app.auth.api_key import ApiKeyContext, require_api_key
from app.auth.session import require_session
from app.core.reply.schema import ReplyDraft
from app.main import create_app

_CTX = ApiKeyContext(org_id="test-org", api_key_id="k", key_name="k", usage_record_id="u")
_BODY = {"text": "The stitching came apart after one wash.", "tone": "apologetic"}
_REQ = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")


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


@pytest.fixture(autouse=True)
def _reply_drafting_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests exercise drafting, so the ENABLE_REPLY_DRAFTING kill switch is set on."""
    monkeypatch.setattr(
        "app.api.v2.reply.get_settings", lambda: SimpleNamespace(enable_reply_drafting=True)
    )


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    from app.api.bff import router as bff_router
    from app.api.v2 import reply as v2_reply

    bff_router._DRAFT_CACHE.clear()
    v2_reply._DRAFT_CACHE.clear()


def _status_error(code: int) -> groq.APIStatusError:
    return groq.APIStatusError(
        "secret provider text sk-leak", response=httpx.Response(code, request=_REQ), body=None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (_status_error(500), 502, "reply_upstream_error"),
        (_status_error(401), 502, "reply_upstream_error"),
        (groq.APIConnectionError(request=_REQ), 503, "reply_upstream_unreachable"),
        (ValueError("boom /home/x/secret.py"), 500, "reply_internal_error"),
    ],
)
async def test_bff_reply_non_quota_errors_are_typed_not_bare_500(
    client: httpx.AsyncClient, exc: Exception, status: int, code: str
) -> None:
    with patch("app.api.bff.router.draft_reply", new=AsyncMock(side_effect=exc)):
        resp = await client.post("/bff/reply", json=_BODY)
    assert resp.status_code == status
    body = resp.json()
    assert body["code"] == code
    assert body["correlation_id"] == resp.headers["X-Correlation-ID"]
    assert isinstance(body["detail"], str)
    for leaked in ("sk-leak", "secret", "Traceback", "boom"):
        assert leaked not in resp.text
    if status == 503:
        assert int(resp.headers["Retry-After"]) > 0


@pytest.mark.asyncio
async def test_retry_after_is_exposed_to_browsers_via_cors(client: httpx.AsyncClient) -> None:
    """A cross-origin fetch() can only read headers listed in Access-Control-Expose-Headers."""
    from app.core.reply.engine import VernacularModelUnavailableError

    origin = "http://localhost:5173"
    with patch(
        "app.api.bff.router.draft_reply",
        new=AsyncMock(side_effect=VernacularModelUnavailableError("x", retry_after=77)),
    ):
        resp = await client.post("/bff/reply", json=_BODY, headers={"Origin": origin})
    if "access-control-allow-origin" not in resp.headers:
        pytest.skip("test origin not in default ALLOWED_ORIGINS")
    assert resp.headers["Retry-After"] == "77"
    exposed = resp.headers["access-control-expose-headers"].lower()
    assert "retry-after" in exposed


def _draft() -> ReplyDraft:
    return ReplyDraft(
        reply_text="ok",
        language="en",
        tone="apologetic",
        grounded_on=[],
        caveats=[],
        model_used="m",
        drafted_at="2026-10-08T00:00:00Z",
    )


@pytest.mark.asyncio
async def test_v2_batch_reports_failed_items_with_index_and_code(
    client: httpx.AsyncClient,
) -> None:
    async def fake(req, ctx):  # noqa: ANN001, ANN202
        if req.text.startswith("bad-http"):
            raise _status_error(500)
        if req.text.startswith("bad-rt"):
            raise RuntimeError("upstream timeout")
        return _draft()

    reviews = [
        {"text": "good one", "tone": "apologetic"},
        {"text": "bad-http item", "tone": "apologetic"},
        {"text": "another good", "tone": "apologetic"},
        {"text": "bad-rt item", "tone": "apologetic"},
    ]
    with patch("app.api.v2.reply._run_draft", new=fake):
        resp = await client.post("/v2/reply/batch", json={"reviews": reviews})
    assert resp.status_code == 200
    assert len(resp.json()) == 2
    failed = json.loads(resp.headers["X-Failed-Items"])
    assert failed == [
        {"index": 1, "code": "reply_upstream_error"},
        {"index": 3, "code": "reply_upstream_unavailable"},
    ]


@pytest.mark.asyncio
async def test_v2_batch_all_failed_is_503_listing_every_index(client: httpx.AsyncClient) -> None:
    with patch("app.api.v2.reply._run_draft", new=AsyncMock(side_effect=RuntimeError("down"))):
        resp = await client.post(
            "/v2/reply/batch",
            json={
                "reviews": [
                    {"text": "a", "tone": "apologetic"},
                    {"text": "b", "tone": "apologetic"},
                ]
            },
        )
    assert resp.status_code == 503
    body = resp.json()
    assert body["code"] == "reply_batch_all_failed"
    assert [f["index"] for f in body["failed_items"]] == [0, 1]
    assert "Retry-After" in resp.headers


@pytest.mark.asyncio
async def test_v2_single_non_quota_error_is_typed(client: httpx.AsyncClient) -> None:
    with patch("app.api.v2.reply._run_draft", new=AsyncMock(side_effect=_status_error(500))):
        resp = await client.post("/v2/reply", json=_BODY)
    assert resp.status_code == 502
    assert resp.json()["code"] == "reply_upstream_error"
