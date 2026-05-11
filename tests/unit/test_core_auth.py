"""Unit tests for app.core.auth — v1 API key dependency."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.core.auth import require_api_key


def _patch_settings(api_key: str = "test-key"):
    from app.core.config import Settings

    s = Settings.model_construct(api_key=api_key)
    return patch("app.core.auth.get_settings", return_value=s)


async def test_no_auth_configured_raises_runtime_error() -> None:
    with _patch_settings(api_key=""):
        with pytest.raises(RuntimeError, match="API_KEY env var not configured"):
            await require_api_key(api_key=None)


async def test_valid_key_passes() -> None:
    with _patch_settings(api_key="test-key"):
        result = await require_api_key(api_key="test-key")
    assert result == "test-key"


async def test_wrong_key_raises_401() -> None:
    with _patch_settings(api_key="test-key"):
        with pytest.raises(HTTPException) as exc:
            await require_api_key(api_key="wrong-key")
    assert exc.value.status_code == 401


async def test_missing_key_raises_401() -> None:
    with _patch_settings(api_key="test-key"):
        with pytest.raises(HTTPException) as exc:
            await require_api_key(api_key=None)
    assert exc.value.status_code == 401
