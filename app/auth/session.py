"""BFF session auth: Supabase JWT → ApiKeyContext.

This module validates a Supabase Bearer JWT and derives an ApiKeyContext by
looking up the org via organization_members + api_keys.  The raw API key
is NEVER retrieved or used here — the BFF path bypasses argon2id entirely and
constructs the context directly from the DB row.

Security invariants (verified by test_bff_session.py):
  - Raw API key material must not appear in this module
  - Stored key hash field must not appear in this module
"""

from __future__ import annotations

import asyncio

import psycopg2
import structlog
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.api_key import ApiKeyContext
from app.auth.signup import verify_supabase_jwt
from app.core.config import get_settings

_BEARER = HTTPBearer(auto_error=False)

log = structlog.get_logger(__name__)


def _db_connect() -> psycopg2.extensions.connection:
    settings = get_settings()
    # Pooler (transaction mode) — same URL used by api_key.py.
    return psycopg2.connect(settings.supabase_database_url)


def _lookup_and_record_for_session(user_id: str) -> ApiKeyContext:
    """Sync: org lookup via JWT user_id → monthly quota check → usage record.

    SELECT FOR UPDATE on the api_keys row serializes concurrent requests for the
    same key, preventing over-admission without a TOCTOU race — same guarantee
    as the api_key.py path.

    Run via asyncio.to_thread — never call directly from async code.
    """
    conn = _db_connect()
    conn.autocommit = False
    try:
        cur = conn.cursor()

        # 1. Resolve org + active key for this Supabase user, row-locked.
        #    WHERE clause binds to user_id from the verified JWT — never from
        #    request body — so cross-org access via forged params is structurally impossible.
        cur.execute(
            """
            SELECT api_keys.id, api_keys.org_id, api_keys.name, api_keys.quota
            FROM public.organization_members
            JOIN public.api_keys ON api_keys.org_id = organization_members.org_id
            WHERE organization_members.user_id = %s
              AND api_keys.revoked_at IS NULL
            ORDER BY api_keys.created_at DESC
            LIMIT 1
            FOR UPDATE OF api_keys
            """,
            (user_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No organization found. Call /auth/provision first.",
            )

        key_id, org_id, key_name, quota = row

        # 2. Monthly usage count — identical logic to api_key.py:97-104.
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
                "Use /bff/quota-requests to register interest in a higher limit.",
            )

        # 3. Stamp last_used_at — same as api_key.py:107-109.
        cur.execute(
            "UPDATE public.api_keys SET last_used_at = now() WHERE id = %s",
            (str(key_id),),
        )

        # 4. Record the call — same as api_key.py:113-117.
        cur.execute(
            "INSERT INTO public.usage_records (org_id, api_key_id) VALUES (%s, %s) RETURNING id",
            (str(org_id), str(key_id)),
        )
        (usage_record_id,) = cur.fetchone()

        conn.commit()
        log.info("session.lookup_ok", org_id=str(org_id), user_id=user_id)
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


def _lookup_context_for_read(user_id: str) -> ApiKeyContext:
    """Sync: org lookup via JWT user_id — no quota check, no usage record.

    Used for read-only BFF endpoints so previously-generated data is always
    accessible even when an org has hit its monthly quota.
    Run via asyncio.to_thread — never call directly from async code.
    """
    conn = _db_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT api_keys.id, api_keys.org_id, api_keys.name
            FROM public.organization_members
            JOIN public.api_keys ON api_keys.org_id = organization_members.org_id
            WHERE organization_members.user_id = %s
              AND api_keys.revoked_at IS NULL
            ORDER BY api_keys.created_at DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No organization found. Call /auth/provision first.",
            )
        key_id, org_id, key_name = row
        log.debug("session.read_ok", org_id=str(org_id), user_id=user_id)
        return ApiKeyContext(
            org_id=str(org_id),
            api_key_id=str(key_id),
            key_name=key_name,
            usage_record_id="",  # read path: no usage record created
        )
    except Exception:
        raise
    finally:
        conn.close()


async def require_session(
    bearer: HTTPAuthorizationCredentials | None = Security(_BEARER),
) -> ApiKeyContext:
    """FastAPI dependency for write/LLM BFF endpoints (ingest, authenticity, reply).

    Accepts:
      Authorization: Bearer <supabase_jwt>

    Raises 401 for missing/invalid/expired token, 403 if not yet provisioned,
    429 if monthly quota exceeded.
    """
    if bearer is None or not bearer.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization: Bearer <supabase_token>.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    jwt = bearer.credentials
    # verify_supabase_jwt already raises 401 on invalid/expired tokens.
    user = await verify_supabase_jwt(jwt)

    return await asyncio.to_thread(_lookup_and_record_for_session, str(user.id))


async def require_session_read(
    bearer: HTTPAuthorizationCredentials | None = Security(_BEARER),
) -> ApiKeyContext:
    """FastAPI dependency for read-only BFF endpoints (reviews, insights, account, etc.).

    Same JWT validation as require_session but no quota check and no usage record.
    Ensures data is always accessible even when an org has hit its monthly limit —
    the quota gate caps new processing only, never read access.
    """
    if bearer is None or not bearer.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization: Bearer <supabase_token>.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    jwt = bearer.credentials
    user = await verify_supabase_jwt(jwt)

    return await asyncio.to_thread(_lookup_context_for_read, str(user.id))
