"""HTTP Basic auth dependency for /admin/* endpoints."""
from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.core.config import get_settings

_BASIC = HTTPBasic()
_PH = PasswordHasher()


def require_admin(credentials: HTTPBasicCredentials = Depends(_BASIC)) -> None:
    """Verify admin HTTP Basic credentials.

    Username comparison is constant-time (secrets.compare_digest).
    Password is always verified via argon2id regardless of username result,
    preventing username enumeration through timing.
    """
    settings = get_settings()
    username_ok = secrets.compare_digest(
        credentials.username.encode("utf-8"),
        settings.admin_username.encode("utf-8"),
    )
    try:
        _PH.verify(settings.admin_password_hash, credentials.password)
        password_ok = True
    except (VerifyMismatchError, VerificationError, Exception):
        password_ok = False
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials.",
            headers={"WWW-Authenticate": "Basic"},
        )
