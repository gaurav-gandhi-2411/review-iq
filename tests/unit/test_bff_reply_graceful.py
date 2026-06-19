"""Tests that /bff/reply returns friendly 503 when the large model is capped.

The reply feature is the daily-value differentiator for the free tier.
Its failure mode must feel intentional, not broken.
"""
from __future__ import annotations

import pytest
import httpx
from unittest.mock import AsyncMock, patch

from app.auth.api_key import ApiKeyContext
from app.auth.session import require_session
from app.main import create_app

_CTX = ApiKeyContext(
    org_id="test-org",
    api_key_id="test-key-id",
    key_name="test-key",
    usage_record_id="test-usage-id",
)


@pytest.fixture()
async def client() -> httpx.AsyncClient:
    app = create_app()
    app.dependency_overrides[require_session] = lambda: _CTX
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_reply_groq_capped_returns_503_with_friendly_message(client: httpx.AsyncClient) -> None:
    """Groq quota exhausted → 503 with human-readable message, not stack trace."""
    from app.core.reply.engine import VernacularModelUnavailableError

    with patch(
        "app.api.bff.router.draft_reply",
        new=AsyncMock(side_effect=VernacularModelUnavailableError("quota exhausted")),
    ):
        resp = await client.post(
            "/bff/reply",
            json={"text": "The stitching came apart after one wash.", "tone": "apologetic"},
        )

    assert resp.status_code == 503
    body = resp.json()
    assert "detail" in body
    # Must be human-readable — no stack trace markers
    assert "try again" in body["detail"].lower() or "shortly" in body["detail"].lower()
    assert "traceback" not in body["detail"].lower()
    assert "VernacularModelUnavailableError" not in body["detail"]
    assert "Retry-After" in resp.headers


@pytest.mark.asyncio
async def test_reply_runtime_error_also_returns_503_friendly(client: httpx.AsyncClient) -> None:
    """RuntimeError from reply engine (e.g. model timeout) → friendly 503."""
    with patch(
        "app.api.bff.router.draft_reply",
        new=AsyncMock(side_effect=RuntimeError("upstream timeout")),
    ):
        resp = await client.post(
            "/bff/reply",
            json={"text": "Packaging was damaged on arrival.", "tone": "professional"},
        )
    assert resp.status_code == 503
    body = resp.json()
    assert "try again" in body["detail"].lower() or "shortly" in body["detail"].lower()
    assert "RuntimeError" not in body["detail"]


@pytest.mark.asyncio
async def test_reply_success_returns_reply_text(client: httpx.AsyncClient) -> None:
    """Happy path: draft_reply returns a ReplyDraft → 200 with reply_text."""
    from datetime import datetime, UTC
    from app.core.reply.schema import ReplyDraft, ReplyTone

    mock_draft = ReplyDraft(
        reply_text="Thank you for your feedback! We're sorry about the stitching issue.",
        language="en",
        tone=ReplyTone.apologetic,
        grounded_on=["stitching", "one wash"],
        caveats=[],
        model_used="test-model",
        drafted_at=datetime.now(UTC),
    )
    with patch("app.api.bff.router.draft_reply", new=AsyncMock(return_value=(mock_draft, 100, 50))):
        with patch("app.api.bff.router.update_usage_tokens", new=AsyncMock()):
            with patch("app.api.bff.router.get_authenticity_audit_by_hash_pg", return_value=None):
                resp = await client.post(
                    "/bff/reply",
                    json={"text": "The stitching came apart after one wash.", "tone": "apologetic"},
                )
    assert resp.status_code == 200
    body = resp.json()
    assert "reply_text" in body
    assert "stitching" in body["reply_text"].lower() or len(body["reply_text"]) > 10
