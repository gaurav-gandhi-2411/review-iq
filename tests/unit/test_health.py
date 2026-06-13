"""Unit tests for the /health endpoint.

All DB and provider dependencies are mocked — no live connections made.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# /health — DB ping succeeds (SQLite path, default local env)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_db_ok(client: AsyncClient) -> None:
    """When the DB ping succeeds, /health returns 200 with db=ok."""
    with patch("app.api.ops._ping_db", new_callable=AsyncMock) as mock_ping:
        mock_ping.return_value = ("ok", None)
        response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    # Top-level status field preserved for backward compatibility
    assert "status" in body


# ---------------------------------------------------------------------------
# /health — DB ping fails → 503
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_db_unreachable_returns_503(client: AsyncClient) -> None:
    """When the DB ping fails, /health must return 503, not 200."""
    with patch("app.api.ops._ping_db", new_callable=AsyncMock) as mock_ping:
        mock_ping.return_value = ("unreachable", "connection refused")
        response = await client.get("/health")

    assert response.status_code == 503, f"Expected 503 when DB is down, got {response.status_code}"
    body = response.json()
    assert body["status"] == "unhealthy"
    assert body["db"] == "unreachable"
    assert "detail" in body
    assert "connection refused" in body["detail"]


# ---------------------------------------------------------------------------
# /health — default (no ?deep=1) makes NO provider network call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_default_no_provider_network_call(client: AsyncClient) -> None:
    """Default /health must not call _provider_status_deep (no network)."""
    with (
        patch("app.api.ops._ping_db", new_callable=AsyncMock) as mock_ping,
        patch("app.api.ops._provider_status_deep", new_callable=AsyncMock) as mock_deep,
    ):
        mock_ping.return_value = ("ok", None)
        response = await client.get("/health")

    assert response.status_code == 200
    # Deep probe must NOT have been called
    mock_deep.assert_not_called()
    body = response.json()
    # Provider is reported by credential presence (shallow check)
    assert "provider" in body


# ---------------------------------------------------------------------------
# /health?deep=1 — performs provider network call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_deep_calls_provider_probe(client: AsyncClient) -> None:
    """With ?deep=1, /health must invoke _provider_status_deep."""
    with (
        patch("app.api.ops._ping_db", new_callable=AsyncMock) as mock_ping,
        patch("app.api.ops._provider_status_deep", new_callable=AsyncMock) as mock_deep,
    ):
        mock_ping.return_value = ("ok", None)
        mock_deep.return_value = ("ok", None)
        response = await client.get("/health?deep=1")

    assert response.status_code == 200
    mock_deep.assert_called_once()
    body = response.json()
    assert body["provider"] == "ok"


# ---------------------------------------------------------------------------
# /health?deep=1 — provider unreachable still returns 200 if DB is ok
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_deep_provider_unreachable_db_ok_returns_200(client: AsyncClient) -> None:
    """DB healthy + provider unreachable → still 200 (DB is the primary liveness signal)."""
    with (
        patch("app.api.ops._ping_db", new_callable=AsyncMock) as mock_ping,
        patch("app.api.ops._provider_status_deep", new_callable=AsyncMock) as mock_deep,
    ):
        mock_ping.return_value = ("ok", None)
        mock_deep.return_value = ("unreachable", "timeout")
        response = await client.get("/health?deep=1")

    assert response.status_code == 200
    body = response.json()
    assert body["db"] == "ok"
    assert body["provider"] == "unreachable"
    assert body["provider_detail"] == "timeout"


# ---------------------------------------------------------------------------
# /health — DB unreachable AND deep=1 → 503
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_deep_db_down_returns_503(client: AsyncClient) -> None:
    """When DB is down, /health?deep=1 must return 503 regardless of provider status."""
    with (
        patch("app.api.ops._ping_db", new_callable=AsyncMock) as mock_ping,
        patch("app.api.ops._provider_status_deep", new_callable=AsyncMock) as mock_deep,
    ):
        mock_ping.return_value = ("unreachable", "host unreachable")
        mock_deep.return_value = ("ok", None)
        response = await client.get("/health?deep=1")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unhealthy"


# ---------------------------------------------------------------------------
# /metrics — still works
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics(client: AsyncClient) -> None:
    """Prometheus /metrics endpoint should still return 200."""
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
