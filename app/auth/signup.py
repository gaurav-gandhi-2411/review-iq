"""Supabase magic-link callback → org + riq_live_ key provisioning."""

from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any

import psycopg2
import structlog
from fastapi import APIRouter, Header, HTTPException, Request, status

from app.auth.keygen import generate_api_key
from app.core.config import get_settings
from app.core.rate_limit import limiter

router = APIRouter(prefix="/auth", tags=["auth"])
log = structlog.get_logger(__name__)


def _db_connect() -> psycopg2.extensions.connection:
    return psycopg2.connect(get_settings().supabase_database_url)


def _get_supabase_admin() -> Any:  # supabase Client type is untyped
    from supabase import create_client  # local import keeps startup fast when creds absent

    s = get_settings()
    return create_client(s.supabase_url, s.supabase_service_role_key)


async def verify_supabase_jwt(jwt: str) -> Any:
    """Verify a Supabase access token; return the User object."""
    client = _get_supabase_admin()
    try:
        response = await asyncio.to_thread(client.auth.get_user, jwt)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Supabase token.",
        ) from exc
    if response is None or response.user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Supabase token.",
        )
    return response.user


def _get_org_for_user(user_id: str) -> dict[str, object] | None:
    """Return org + key info for this Supabase user, or None if not yet provisioned."""
    conn = _db_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT o.id, ak.key_prefix, ak.quota
            FROM public.organization_members om
            JOIN public.organizations o ON o.id = om.org_id
            LEFT JOIN public.api_keys ak
                   ON ak.org_id = o.id AND ak.revoked_at IS NULL
            WHERE om.user_id = %s
            ORDER BY ak.created_at DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cur.fetchone()
        conn.commit()
        if row is None:
            return None
        org_id, key_prefix, quota = row
        return {"org_id": str(org_id), "key_prefix": key_prefix, "quota": quota}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _provision_org_and_key(user_id: str, email: str) -> dict[str, str | int]:
    """Create org + riq_live_ key + owner membership for a first-time user.

    Returns raw_key exactly once — caller must relay it to the user immediately.
    """
    raw_key, key_prefix, key_hash = generate_api_key()

    safe = re.sub(r"[^a-z0-9]", "-", email.split("@")[0].lower())[:20]
    slug = f"{safe}-{uuid.uuid4().hex[:6]}"

    conn = _db_connect()
    try:
        cur = conn.cursor()

        cur.execute(
            "INSERT INTO public.organizations (name, slug) VALUES (%s, %s) RETURNING id",
            (email, slug),
        )
        (org_id,) = cur.fetchone()

        cur.execute(
            """
            INSERT INTO public.api_keys (org_id, key_hash, key_prefix, name, quota)
            VALUES (%s, %s, %s, 'default', 100)
            """,
            (str(org_id), key_hash, key_prefix),
        )

        cur.execute(
            "INSERT INTO public.organization_members (org_id, user_id, role) "
            "VALUES (%s, %s, 'owner')",
            (str(org_id), user_id),
        )

        conn.commit()
        log.info("signup.provisioned", org_id=str(org_id), user_id=user_id)
        return {
            "org_id": str(org_id),
            "key_prefix": key_prefix,
            "raw_key": raw_key,
            "monthly_quota": 100,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@router.post("/provision")
@limiter.limit("10/minute")
async def provision(
    request: Request,
    authorization: str = Header(default="", alias="Authorization"),
) -> dict[str, object]:
    """On first Supabase login, create org + riq_live_ key.

    Pass the Supabase access token as `Authorization: Bearer <token>`.

    Response on first call (status="created"):
      raw_key, key_prefix, org_id, monthly_quota=100

    Response on subsequent calls (status="existing"):
      key_prefix, org_id, monthly_quota (no raw_key — not stored)
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization: Bearer <supabase_token> required.",
        )
    jwt = authorization[len("Bearer ") :]

    user = await verify_supabase_jwt(jwt)

    existing = await asyncio.to_thread(_get_org_for_user, str(user.id))
    if existing:
        return {
            "status": "existing",
            "org_id": existing["org_id"],
            "key_prefix": existing["key_prefix"],
            "monthly_quota": existing["quota"],
        }

    result = await asyncio.to_thread(_provision_org_and_key, str(user.id), user.email or "")
    return {"status": "created", **result}
