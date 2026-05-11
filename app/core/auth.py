"""API key authentication for write endpoints."""

from __future__ import annotations

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.core.config import get_settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(api_key: str | None = Security(_api_key_header)) -> str:
    """FastAPI dependency — enforce API key on write endpoints.

    Raises 401 if the key is absent or invalid.
    """
    settings = get_settings()
    if not settings.api_key:
        raise RuntimeError(
            "API_KEY env var not configured. v1 endpoints require "
            "this to be set. Set API_KEY in .env or do not mount v1 router."
        )
    if not api_key or api_key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key. Set X-API-Key header.",
        )
    return api_key
