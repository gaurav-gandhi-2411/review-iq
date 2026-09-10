"""Tests for v1 query endpoints (GET /reviews and GET /insights)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from app.core.auth import require_api_key as v1_require_api_key
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
        app.dependency_overrides[v1_require_api_key] = lambda: "test-api-key"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as c:
            yield c
        app.dependency_overrides.clear()


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


class TestAuthRequired:
    """Regression coverage for the unauthenticated data-exposure hole (spec Wave 1 §C / D9).

    GET /reviews and GET /insights previously had no auth dependency at all — confirmed live
    against the v1 HF Space returning 200 with no X-API-Key. These tests prove both routes now
    require the same X-API-Key guard as /extract, without changing existing 200 behavior.
    """

    async def test_reviews_missing_auth_returns_401(self, tmp_path: Path) -> None:
        db_path = tmp_path / "reviews_noauth.db"
        db_url = f"sqlite+aiosqlite:///{db_path}"
        mock_settings = type("S", (), {"database_url": db_url})()
        with patch("app.core.storage.get_settings", return_value=mock_settings):
            await migrate()
            app = create_app()
            # Do NOT override auth — use the real require_api_key dependency.
            with patch(
                "app.core.auth.get_settings",
                return_value=type("S", (), {"api_key": "real-secret"})(),
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://test",
                ) as c:
                    response = await c.get("/reviews")
        assert response.status_code == 401

    async def test_reviews_with_valid_key_returns_200(self, tmp_path: Path) -> None:
        db_path = tmp_path / "reviews_authed.db"
        db_url = f"sqlite+aiosqlite:///{db_path}"
        mock_settings = type("S", (), {"database_url": db_url})()
        with patch("app.core.storage.get_settings", return_value=mock_settings):
            await migrate()
            app = create_app()
            with patch(
                "app.core.auth.get_settings",
                return_value=type("S", (), {"api_key": "real-secret"})(),
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://test",
                ) as c:
                    response = await c.get("/reviews", headers={"X-API-Key": "real-secret"})
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0
        assert data["results"] == []

    async def test_insights_missing_auth_returns_401(self, tmp_path: Path) -> None:
        db_path = tmp_path / "insights_noauth.db"
        db_url = f"sqlite+aiosqlite:///{db_path}"
        mock_settings = type("S", (), {"database_url": db_url})()
        with patch("app.core.storage.get_settings", return_value=mock_settings):
            await migrate()
            app = create_app()
            with patch(
                "app.core.auth.get_settings",
                return_value=type("S", (), {"api_key": "real-secret"})(),
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://test",
                ) as c:
                    response = await c.get("/insights")
        assert response.status_code == 401

    async def test_insights_with_valid_key_returns_200(self, tmp_path: Path) -> None:
        db_path = tmp_path / "insights_authed.db"
        db_url = f"sqlite+aiosqlite:///{db_path}"
        mock_settings = type("S", (), {"database_url": db_url})()
        with patch("app.core.storage.get_settings", return_value=mock_settings):
            await migrate()
            app = create_app()
            with patch(
                "app.core.auth.get_settings",
                return_value=type("S", (), {"api_key": "real-secret"})(),
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://test",
                ) as c:
                    response = await c.get("/insights", headers={"X-API-Key": "real-secret"})
        assert response.status_code == 200
        data = response.json()
        assert data["total_extractions"] == 0
