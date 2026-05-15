"""Tests for v1 query endpoints (GET /reviews and GET /insights)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from app.core.storage import migrate
from app.main import create_app


@pytest.fixture()
async def client(tmp_path: Path) -> httpx.AsyncClient:
    db_path = tmp_path / "query_v1_test.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    mock_settings = type("S", (), {"database_url": db_url})()

    with patch("app.core.storage.get_settings", return_value=mock_settings):
        await migrate()
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as c:
            yield c


class TestListReviews:
    async def test_list_reviews_empty(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/reviews")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0
        assert data["results"] == []

    async def test_list_reviews_with_params(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/reviews?sentiment=positive&limit=10&offset=0")
        assert response.status_code == 200
        data = response.json()
        assert "count" in data
        assert "results" in data
        assert data["limit"] == 10
        assert data["offset"] == 0


class TestInsights:
    async def test_insights_empty_db(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/insights")
        assert response.status_code == 200
        data = response.json()
        assert data["total_extractions"] == 0
