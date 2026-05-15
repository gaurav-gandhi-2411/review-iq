"""Unit tests for the dashboard route — verifies the GET / handler executes without error."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from app.core.storage import migrate
from app.main import create_app


@pytest.fixture()
async def client(tmp_path: Path) -> httpx.AsyncClient:
    db_path = tmp_path / "dash_test.db"
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


class TestDashboardRoute:
    async def test_dashboard_returns_200(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/")
        assert response.status_code == 200

    async def test_dashboard_returns_html(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/")
        assert "text/html" in response.headers["content-type"]

    async def test_dashboard_renders_with_empty_db(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/")
        # Page renders successfully even with no data — no 500 errors
        assert response.status_code == 200
        assert len(response.text) > 0
