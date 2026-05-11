"""Unit tests for app.auth.admin — HTTP Basic auth dependency."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from argon2 import PasswordHasher
from fastapi import HTTPException
from fastapi.security import HTTPBasicCredentials

from app.auth.admin import require_admin

_PH = PasswordHasher()
_PASSWORD = "correct-password"
_HASH = _PH.hash(_PASSWORD)


def _creds(username: str = "admin", password: str = _PASSWORD) -> HTTPBasicCredentials:
    return HTTPBasicCredentials(username=username, password=password)


def _patch_settings(username: str = "admin", pw_hash: str = _HASH):
    from app.core.config import Settings

    s = Settings.model_construct(
        admin_username=username,
        admin_password_hash=pw_hash,
    )
    return patch("app.auth.admin.get_settings", return_value=s)


def test_valid_credentials_passes() -> None:
    with _patch_settings():
        require_admin(_creds())  # must not raise


def test_wrong_password_raises_401() -> None:
    with _patch_settings():
        with pytest.raises(HTTPException) as exc:
            require_admin(_creds(password="wrong"))
    assert exc.value.status_code == 401
    assert "WWW-Authenticate" in exc.value.headers


def test_wrong_username_raises_401() -> None:
    with _patch_settings():
        with pytest.raises(HTTPException) as exc:
            require_admin(_creds(username="hacker"))
    assert exc.value.status_code == 401


def test_wrong_username_and_password_raises_401() -> None:
    with _patch_settings():
        with pytest.raises(HTTPException) as exc:
            require_admin(_creds(username="hacker", password="wrong"))
    assert exc.value.status_code == 401


def test_empty_hash_raises_401() -> None:
    with _patch_settings(pw_hash=""):
        with pytest.raises(HTTPException) as exc:
            require_admin(_creds())
    assert exc.value.status_code == 401
