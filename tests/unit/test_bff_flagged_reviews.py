"""Unit tests for GET /bff/authenticity/flagged -- the flagged-review queue.

Mirrors the minimal-smoke-test style used for other new BFF wiring (see
test_bff_insights_batch_defects.py's docstring): proves the session-auth dependency and
org-scoped DB call wiring are correct, not authenticity_audit_summary_pg's SQL itself
(that lives in test_storage_pg.py).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pytest
from app.auth.api_key import ApiKeyContext
from app.auth.session import require_session_read

_ORG_ID = str(uuid.uuid4())
_CTX = ApiKeyContext(
    org_id=_ORG_ID, api_key_id=str(uuid.uuid4()), key_name="test-key", usage_record_id=""
)


@pytest.fixture()
async def client() -> httpx.AsyncClient:
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[require_session_read] = lambda: _CTX
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


async def test_flagged_reviews_happy_path(client: httpx.AsyncClient) -> None:
    fake_row = {
        "review_hash": "a" * 64,
        "score": 0.42,
        "label": "suspicious",
        "flags": ["near_duplicate"],
        "created_at": datetime(2026, 7, 1, tzinfo=UTC),
    }
    with patch(
        "app.api.bff.router.list_flagged_authenticity_audits_pg",
        return_value=[fake_row],
    ) as mock_list:
        resp = await client.get("/bff/authenticity/flagged")

    assert resp.status_code == 200
    body = resp.json()
    assert body["org_id"] == _ORG_ID
    assert body["count"] == 1
    assert body["results"][0]["review_hash"] == "a" * 64
    assert body["results"][0]["label"] == "suspicious"
    assert body["results"][0]["flags"] == ["near_duplicate"]
    # org_id, limit, offset forwarded correctly (default limit=50, offset=0)
    mock_list.assert_called_once_with(_ORG_ID, 50, 0)


async def test_flagged_reviews_empty(client: httpx.AsyncClient) -> None:
    with patch("app.api.bff.router.list_flagged_authenticity_audits_pg", return_value=[]):
        resp = await client.get("/bff/authenticity/flagged")

    assert resp.status_code == 200
    assert resp.json()["results"] == []


async def test_flagged_reviews_limit_capped_at_200(client: httpx.AsyncClient) -> None:
    resp = await client.get("/bff/authenticity/flagged?limit=500")
    assert resp.status_code == 422


async def test_flagged_reviews_custom_limit_offset(client: httpx.AsyncClient) -> None:
    with patch(
        "app.api.bff.router.list_flagged_authenticity_audits_pg", return_value=[]
    ) as mock_list:
        resp = await client.get("/bff/authenticity/flagged?limit=10&offset=20")

    assert resp.status_code == 200
    mock_list.assert_called_once_with(_ORG_ID, 10, 20)
