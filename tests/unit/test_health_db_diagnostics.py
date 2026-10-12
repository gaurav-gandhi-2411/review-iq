"""Regression tests for the 2026-10-10..12 /health db_timeout burst (S23).

Evidence behind these tests (Cloud Run + Cloud Monitoring logs, project reviewiq-prod-260813):
healthy /health median 1.77s (n=19,745), 23 x 503 at exactly 3.00s = the old outer guard, and
every ``health.db_unreachable`` event carried ``error=""`` so none could be diagnosed.
No live connections are made: psycopg2 is replaced with a fake.
"""

from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import structlog
from app.api import ops
from httpx import AsyncClient

HEALTHY_PING_MEDIAN_S = 1.77  # measured, see module docstring
UPTIME_CHECK_TIMEOUT_S = 10  # live Cloud Monitoring check `api-health`


def _fake_psycopg2(connect_delay: float = 0.0, release: threading.Event | None = None) -> Any:
    class _Cur:
        def execute(self, _sql: str) -> None:
            return None

    class _Conn:
        def cursor(self) -> _Cur:
            return _Cur()

        def close(self) -> None:
            return None

    def _connect(_dsn: str, connect_timeout: int) -> _Conn:  # noqa: ARG001
        if release is not None:
            release.wait(timeout=5)
        time.sleep(connect_delay)
        return _Conn()

    return SimpleNamespace(connect=_connect)


def test_ping_budget_leaves_headroom_over_healthy_latency() -> None:
    """The old 3s guard was 1.7x the healthy ping; one stalled TCP handshake exhausted it."""
    assert ops._PG_PING_TIMEOUT_SECONDS >= 3 * HEALTHY_PING_MEDIAN_S
    assert ops._PG_PING_CONNECT_TIMEOUT_SECONDS < ops._PG_PING_TIMEOUT_SECONDS
    # A real outage must still answer 503 from the app before the probe gives up.
    assert ops._PG_PING_TIMEOUT_SECONDS < UPTIME_CHECK_TIMEOUT_S


@pytest.mark.asyncio
async def test_ping_timeout_carries_stage_diagnostics() -> None:
    """A stalled connect must report stage=connect and elapsed_ms (asyncio's own message is '')."""
    release = threading.Event()
    try:
        with (
            patch.dict(sys.modules, {"psycopg2": _fake_psycopg2(release=release)}),
            patch.object(ops, "_PG_PING_TIMEOUT_SECONDS", 0.2),
            pytest.raises(TimeoutError) as excinfo,
        ):
            await ops._ping_postgres("postgresql://unused")
    finally:
        release.set()
    assert str(excinfo.value) == ""  # the exact blind spot of the original events
    diag = excinfo.value.ping_diag  # type: ignore[attr-defined]
    assert diag["stage"] == "connect"
    assert diag["elapsed_ms"] >= 200
    assert "connect_ms" not in diag


@pytest.mark.asyncio
async def test_slow_but_successful_ping_is_logged_not_failed() -> None:
    """A ping slower than the slow threshold but inside the budget passes and leaves a trace."""
    with (
        patch.dict(sys.modules, {"psycopg2": _fake_psycopg2(connect_delay=0.3)}),
        patch.object(ops, "_PG_PING_SLOW_SECONDS", 0.1),
        structlog.testing.capture_logs() as logs,
    ):
        await ops._ping_postgres("postgresql://unused")
    slow = [e for e in logs if e["event"] == "health.db_slow"]
    assert len(slow) == 1
    assert slow[0]["connect_ms"] >= 300


@pytest.mark.asyncio
async def test_health_event_logs_exception_type_and_stage(client: AsyncClient) -> None:
    """health.db_unreachable must carry exc_type + stage so a timeout is attributable."""
    exc = TimeoutError()
    exc.ping_diag = {"stage": "query", "connect_ms": 1750, "elapsed_ms": 8001}  # type: ignore[attr-defined]
    with (
        patch("app.api.ops._ping_db", new_callable=AsyncMock) as mock_ping,
        structlog.testing.capture_logs() as logs,
    ):
        mock_ping.return_value = ("unreachable", "db_timeout", exc)
        response = await client.get("/health")
    assert response.status_code == 503
    event = next(e for e in logs if e["event"] == "health.db_unreachable")
    assert event["exc_type"] == "TimeoutError"
    assert event["stage"] == "query"
    assert event["connect_ms"] == 1750
    assert event["elapsed_ms"] == 8001
    assert "stage" not in response.json()  # diagnostics stay server-side
