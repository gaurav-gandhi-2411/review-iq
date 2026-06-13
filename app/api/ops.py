"""Ops endpoints: /health and /metrics.

/health actively probes the configured database to surface real outages.
The default check avoids calling LLM providers (uptime pollers hit /health
frequently; a live LLM call on every poll would drain Groq's free-tier daily
cap).  A REAL provider reachability probe is available behind ?deep=1.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from starlette import status

from app.core.config import get_settings

log = structlog.get_logger(__name__)

router = APIRouter(tags=["ops"])

# ---------------------------------------------------------------------------
# Internal DB ping helpers
# ---------------------------------------------------------------------------

_DB_PING_TIMEOUT_SECONDS = 2  # short-circuit so /health never hangs


async def _ping_postgres(dsn: str) -> None:
    """Perform a cheap SELECT 1 against Postgres with a hard timeout.

    Raises on any connection or query error.  Uses psycopg2 via
    asyncio.to_thread so it does not block the event loop.
    """
    import psycopg2

    def _sync_ping() -> None:
        conn = psycopg2.connect(dsn, connect_timeout=_DB_PING_TIMEOUT_SECONDS)
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
        finally:
            conn.close()

    await asyncio.wait_for(
        asyncio.to_thread(_sync_ping),
        timeout=_DB_PING_TIMEOUT_SECONDS + 1,  # outer guard: thread connect_timeout + 1s
    )


async def _ping_sqlite(db_url: str) -> None:
    """Perform a cheap SELECT 1 against the local SQLite file.

    Raises on any error (e.g., corrupt file, path not writable).
    """
    import aiosqlite

    # sqlite+aiosqlite:///./path  →  strip driver prefix
    path = db_url.replace("sqlite+aiosqlite:///", "")

    async def _do_ping() -> None:
        async with aiosqlite.connect(path) as db:
            await db.execute("SELECT 1")

    await asyncio.wait_for(_do_ping(), timeout=_DB_PING_TIMEOUT_SECONDS)


async def _ping_db() -> tuple[str, str | None]:
    """Ping the right DB backend based on current settings.

    Returns:
        ("ok", None)            — DB is reachable.
        ("unreachable", reason) — DB is down; reason is a short string.
    """
    settings = get_settings()

    # v2/cloud-run path: Postgres via supabase_database_url
    if settings.supabase_database_url:
        try:
            await _ping_postgres(settings.supabase_database_url)
            return "ok", None
        except Exception as exc:  # noqa: BLE001
            return "unreachable", str(exc)[:200]  # truncate; DSN may contain secrets

    # v1/local path: SQLite
    db_url = settings.database_url
    try:
        await _ping_sqlite(db_url)
        return "ok", None
    except Exception as exc:  # noqa: BLE001
        return "unreachable", str(exc)[:200]


# ---------------------------------------------------------------------------
# Provider status helpers (credential-presence check; no network call)
# ---------------------------------------------------------------------------


def _provider_status_shallow() -> str:
    """Return 'configured' when a provider credential is present, else 'not_configured'.

    Deliberately avoids any network call — uptime pollers hit /health many
    times per minute; a live LLM call on every poll would drain the Groq
    free-tier daily cap and introduce latency variance into the health probe.
    """
    settings = get_settings()
    return "configured" if settings.groq_api_key else "not_configured"


async def _provider_status_deep() -> tuple[str, str | None]:
    """Perform a minimal REAL reachability probe against the configured provider.

    Only called when ?deep=1 is explicitly requested.  Uses the Groq models/list
    endpoint (a GET with no tokens consumed) as a lightweight connectivity check.

    Returns:
        ("ok", None)         — provider is reachable.
        ("unreachable", why) — provider is unreachable or key is invalid.
    """
    settings = get_settings()
    if not settings.groq_api_key:
        return "not_configured", None

    try:
        # groq.AsyncGroq.models.list() is a cheap GET with no token cost.
        from groq import AsyncGroq

        client = AsyncGroq(api_key=settings.groq_api_key)
        await asyncio.wait_for(client.models.list(), timeout=5.0)
        return "ok", None
    except Exception as exc:  # noqa: BLE001
        return "unreachable", str(exc)[:200]


# ---------------------------------------------------------------------------
# /health endpoint
# ---------------------------------------------------------------------------


@router.get("/health")
async def health(
    deep: bool = Query(default=False, description="Run real provider reachability probe"),
) -> JSONResponse:
    """Health check.

    Default (no ?deep=1): verifies DB reachability only; provider is reported
    based on credential presence (no network call).

    With ?deep=1: also performs a live provider reachability probe.
    """
    settings = get_settings()
    db_status, db_detail = await _ping_db()

    # Determine which DB backend is active for the response body
    db_backend = "postgres" if settings.supabase_database_url else "sqlite"

    body: dict[str, Any] = {
        "status": "ok" if db_status == "ok" else "unhealthy",
        "db": db_status,
        "db_backend": db_backend,
    }

    if db_detail:
        body["detail"] = db_detail
        log.error(
            "health.db_unreachable",
            db_backend=db_backend,
            reason=db_detail,
        )

    # Provider check — deep probe only when explicitly requested
    if deep:
        prov_status, prov_detail = await _provider_status_deep()
        body["provider"] = prov_status
        if prov_detail:
            body["provider_detail"] = prov_detail
            log.warning(
                "health.provider_unreachable",
                provider="groq",
                reason=prov_detail,
            )
    else:
        body["provider"] = _provider_status_shallow()

    http_status = status.HTTP_200_OK if db_status == "ok" else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(content=body, status_code=http_status)
