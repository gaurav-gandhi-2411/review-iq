"""Minimal authenticated account page — key prefix, usage, regenerate."""

from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, Header, HTTPException, status

from app.auth.keygen import generate_api_key
from app.auth.signup import _db_connect, verify_supabase_jwt

router = APIRouter(prefix="/account", tags=["account"])
log = structlog.get_logger(__name__)


async def _require_user_id(authorization: str) -> str:
    """Extract + verify Bearer JWT; return Supabase user_id."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization: Bearer <supabase_token> required.",
        )
    jwt = authorization[len("Bearer ") :]
    user = await verify_supabase_jwt(jwt)
    return str(user.id)


def _fetch_account(user_id: str) -> dict[str, object]:
    conn = _db_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT ak.key_prefix, ak.quota, ak.id as key_id, o.id as org_id
            FROM public.organization_members om
            JOIN public.organizations o ON o.id = om.org_id
            JOIN public.api_keys ak
                   ON ak.org_id = o.id AND ak.revoked_at IS NULL
            WHERE om.user_id = %s
            ORDER BY ak.created_at DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cur.fetchone()
        if row is None:
            conn.commit()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No account found. Complete sign-up first via POST /auth/provision.",
            )
        key_prefix, quota, key_id, org_id = row

        cur.execute(
            """
            SELECT COUNT(*) FROM public.usage_records
            WHERE api_key_id = %s
              AND date_trunc('month', created_at) = date_trunc('month', now())
            """,
            (str(key_id),),
        )
        (monthly_usage,) = cur.fetchone()
        conn.commit()
        return {
            "org_id": str(org_id),
            "key_prefix": key_prefix,
            "monthly_quota": quota,
            "monthly_usage": int(monthly_usage),
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _do_regenerate(user_id: str) -> dict[str, object]:
    """Revoke current key; issue a new riq_live_ key. raw_key shown once."""
    raw_key, key_prefix, key_hash = generate_api_key()

    conn = _db_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT ak.id, o.id as org_id
            FROM public.organization_members om
            JOIN public.organizations o ON o.id = om.org_id
            JOIN public.api_keys ak
                   ON ak.org_id = o.id AND ak.revoked_at IS NULL
            WHERE om.user_id = %s
            ORDER BY ak.created_at DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No active key to regenerate. Complete sign-up first.",
            )
        old_key_id, org_id = row

        cur.execute(
            "UPDATE public.api_keys SET revoked_at = now() WHERE id = %s",
            (str(old_key_id),),
        )
        cur.execute(
            """
            INSERT INTO public.api_keys (org_id, key_hash, key_prefix, name, quota)
            VALUES (%s, %s, %s, 'default', 100)
            """,
            (str(org_id), key_hash, key_prefix),
        )
        conn.commit()
        log.info("account.key_regenerated", org_id=str(org_id), user_id=user_id)
        return {"key_prefix": key_prefix, "raw_key": raw_key, "monthly_quota": 100}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@router.get("")
async def get_account(
    authorization: str = Header(default="", alias="Authorization"),
) -> dict[str, object]:
    """Return account info: key prefix, monthly usage out of quota."""
    user_id = await _require_user_id(authorization)
    return await asyncio.to_thread(_fetch_account, user_id)


@router.post("/regenerate-key")
async def regenerate_key(
    authorization: str = Header(default="", alias="Authorization"),
) -> dict[str, object]:
    """Revoke current riq_live_ key and issue a replacement (shown once)."""
    user_id = await _require_user_id(authorization)
    return await asyncio.to_thread(_do_regenerate, user_id)
