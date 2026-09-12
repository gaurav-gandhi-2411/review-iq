"""Unit tests for GET /bff/export/reviews (CSV/JSON export).

Minimal smoke coverage for new wiring, matching the style established by
test_bff_insights_batch_defects.py -- proves session-auth, format switching, and the
truncation header, not list_extractions_pg's SQL itself (already covered elsewhere).
"""

from __future__ import annotations

import csv
import io
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

_FAKE_ROW = {
    "review_text": "Great product, works well",
    "product": "Widget",
    "stars": 5,
    "stars_inferred": False,
    "buy_again": True,
    "sentiment": "positive",
    "urgency": "low",
    "language": "en",
    "review_length_chars": 26,
    "confidence": 0.9,
    "topics": ["quality"],
    "competitor_mentions": [],
    "pros": ["durable"],
    "cons": [],
    "feature_requests": [],
    "created_at": datetime(2026, 7, 1, tzinfo=UTC),
    "review_date": None,
}


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


async def test_export_csv_happy_path(client: httpx.AsyncClient) -> None:
    with patch("app.api.bff.router.list_extractions_pg", return_value=[_FAKE_ROW]):
        resp = await client.get("/bff/export/reviews?format=csv")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    assert "X-Truncated" not in resp.headers

    reader = csv.reader(io.StringIO(resp.text))
    rows = list(reader)
    assert rows[0][0] == "review_text"  # header row
    assert rows[1][1] == "Widget"  # product column
    assert rows[1][10] == "quality"  # topics list flattened to a scalar


async def test_export_json_happy_path(client: httpx.AsyncClient) -> None:
    with patch("app.api.bff.router.list_extractions_pg", return_value=[_FAKE_ROW]):
        resp = await client.get("/bff/export/reviews?format=json")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert isinstance(body, list)
    assert body[0]["product"] == "Widget"
    assert body[0]["topics"] == ["quality"]


async def test_export_defaults_to_csv(client: httpx.AsyncClient) -> None:
    with patch("app.api.bff.router.list_extractions_pg", return_value=[]):
        resp = await client.get("/bff/export/reviews")
    assert resp.headers["content-type"].startswith("text/csv")


async def test_export_invalid_format_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.get("/bff/export/reviews?format=xml")
    assert resp.status_code == 422


async def test_export_truncation_header_set_at_cap(client: httpx.AsyncClient) -> None:
    rows = [_FAKE_ROW] * 5000
    with (
        patch("app.api.bff.router.list_extractions_pg", return_value=rows),
        patch("app.api.bff.router._EXPORT_ROW_CAP", 5000),
    ):
        resp = await client.get("/bff/export/reviews?format=json")

    assert resp.headers.get("X-Truncated") == "true"


async def test_export_no_truncation_header_below_cap(client: httpx.AsyncClient) -> None:
    with patch("app.api.bff.router.list_extractions_pg", return_value=[_FAKE_ROW]):
        resp = await client.get("/bff/export/reviews?format=json")
    assert "X-Truncated" not in resp.headers
