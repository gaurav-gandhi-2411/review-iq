"""v2 API key authentication middleware.

Key format  : riq_live_<32 lowercase hex chars>
Lookup      : key_prefix (first 17 chars) indexed → O(1) candidate row
Verification: argon2id.verify(stored_hash, raw_key) — constant-time
Quota       : SELECT FOR UPDATE on api_keys row serializes concurrent requests;
              monthly count derived from usage_records via date_trunc('month').
Usage       : same transaction inserts usage_records (tokens_used=0 placeholder)

Connection  : supabase_database_url (port 6543, PgBouncer transaction mode).
              Direct port 5432 is reserved for migrations and RLS integration
              tests that require session-level GUCs (SET LOCAL ROLE, etc.).
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

import psycopg2
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings

_BEARER = HTTPBearer(auto_error=False)
_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
_KEY_RE = re.compile(r"^riq_live_[0-9a-f]{32}$")
_KEY_PREFIX_LEN = 17  # "riq_live_" + 8 hex chars

_PH = PasswordHasher()  # argon2id; parameters encoded in stored hash, not here


@dataclass(frozen=True)
class ApiKeyContext:
    org_id: str
    api_key_id: str
    key_name: str
    usage_record_id: str


def _db_connect() -> psycopg2.extensions.connection:
    settings = get_settings()
    # Pooler (transaction mode) for app traffic. Direct URL only used by
    # migrations and RLS tests that need session-level GUCs.
    return psycopg2.connect(settings.supabase_database_url)


def _lookup_and_record(raw_key: str) -> ApiKeyContext:
    """Sync: prefix lookup → argon2id verify → monthly quota check → usage record.

    SELECT FOR UPDATE on the api_keys row serializes concurrent requests for the
    same key, preventing over-admission without a TOCTOU race.

    Run via asyncio.to_thread — never call directly from async code.
    """
    conn = _db_connect()
    conn.autocommit = False
    try:
        cur = conn.cursor()

        # 1. Prefix lookup with row lock — serializes concurrent quota checks
        cur.execute(
            "SELECT id, org_id, name, key_hash, quota "
            "FROM public.api_keys WHERE key_prefix = %s AND revoked_at IS NULL FOR UPDATE",
            (raw_key[:_KEY_PREFIX_LEN],),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key not found.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        key_id, org_id, key_name, key_hash, quota = row

        # 2. argon2id verification — constant-time
        try:
            _PH.verify(key_hash, raw_key)
        except (VerifyMismatchError, VerificationError):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key not found.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 3. Monthly usage count — derives from usage_records, resets automatically
        cur.execute(
            "SELECT COUNT(*) FROM public.usage_records "
            "WHERE api_key_id = %s "
            "AND date_trunc('month', created_at) = date_trunc('month', now())",
            (str(key_id),),
        )
        (monthly_count,) = cur.fetchone()
        if monthly_count >= quota:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Monthly quota exceeded ({monthly_count}/{quota}). "
                "Contact support to increase.",
            )

        # 4. Stamp last_used_at (monthly_usage column no longer incremented)
        cur.execute(
            "UPDATE public.api_keys SET last_used_at = now() WHERE id = %s",
            (str(key_id),),
        )

        # 5. Record the call — tokens_in/tokens_out updated after LLM completes
        cur.execute(
            "INSERT INTO public.usage_records (org_id, api_key_id) "
            "VALUES (%s, %s) RETURNING id",
            (str(org_id), str(key_id)),
        )
        (usage_record_id,) = cur.fetchone()
        conn.commit()
        return ApiKeyContext(
            org_id=str(org_id),
            api_key_id=str(key_id),
            key_name=key_name,
            usage_record_id=str(usage_record_id),
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


async def require_api_key(
    bearer: HTTPAuthorizationCredentials | None = Security(_BEARER),
    x_api_key: str | None = Security(_API_KEY_HEADER),
) -> ApiKeyContext:
    """FastAPI dependency for v2 endpoints.

    Accepts:
      Authorization: Bearer riq_live_<32-hex>
      X-API-Key: riq_live_<32-hex>   (Bearer takes precedence)

    Raises 401 for missing/invalid key, 429 for quota exceeded.
    """
    raw_key: str | None = bearer.credentials if bearer is not None else x_api_key

    if not raw_key or not _KEY_RE.match(raw_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed API key. "
            "Use Authorization: Bearer riq_live_<32-hex> or X-API-Key header.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await asyncio.to_thread(_lookup_and_record, raw_key)
