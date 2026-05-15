"""Tests for v2 reviews endpoints with mocked auth and storage."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.auth.api_key import ApiKeyContext, require_api_key
from app.main import create_app

_ORG_ID = str(uuid.uuid4())
_KEY_ID = str(uuid.uuid4())
_USAGE_ID = str(uuid.uuid4())

_CTX = ApiKeyContext(
    org_id=_ORG_ID,
    api_key_id=_KEY_ID,
    key_name="test-key",
    usage_record_id=_USAGE_ID,
)


@pytest.fixture()
async def client() -> httpx.AsyncClient:
    app = create_app()
    app.dependency_overrides[require_api_key] = lambda: _CTX
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c
    app.dependency_overrides.clear()


class TestV2ListReviews:
    async def test_list_reviews_returns_200(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.reviews.list_extractions_pg", return_value=[]):
            response = await client.get("/v2/reviews")
        assert response.status_code == 200
        data = response.json()
        assert data["org_id"] == _ORG_ID
        assert data["count"] == 0
        assert data["results"] == []

    async def test_list_reviews_with_filters(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.reviews.list_extractions_pg", return_value=[]) as mock_list:
            response = await client.get("/v2/reviews?sentiment=positive&limit=20")
        assert response.status_code == 200
        mock_list.assert_called_once()


class TestV2Insights:
    async def test_insights_returns_200(self, client: httpx.AsyncClient) -> None:
        mock_data = {"total_extractions": 0, "sentiment_breakdown": {}, "top_topics": []}
        with patch("app.api.v2.reviews.aggregate_extractions_pg", return_value=mock_data):
            response = await client.get("/v2/insights")
        assert response.status_code == 200
        data = response.json()
        assert data["org_id"] == _ORG_ID
        assert data["total_extractions"] == 0
