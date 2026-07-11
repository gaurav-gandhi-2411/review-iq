"""Minimal authenticated account page — key prefix, usage, regenerate, delete."""

from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel

from app.auth.keygen import generate_api_key
from app.auth.signup import _db_connect, verify_supabase_jwt

router = APIRouter(prefix="/account", tags=["account"])
log = structlog.get_logger(__name__)


class DeleteAccountRequest(BaseModel):
    confirm_slug: str


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
            SELECT ak.key_prefix, ak.quota, ak.id as key_id, o.id as org_id, o.slug
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
        key_prefix, quota, key_id, org_id, slug = row

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
            "slug": slug,
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


def _get_org_id_and_slug(user_id: str) -> tuple[str, str] | None:
    """Return (org_id, slug) for this user's org, or None if not a member of any org.

    Same membership-resolution pattern as _fetch_account -- org_id is NEVER taken
    from a request parameter, only ever resolved server-side from the verified
    JWT's user_id via organization_members.
    """
    conn = _db_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT o.id, o.slug
            FROM public.organization_members om
            JOIN public.organizations o ON o.id = om.org_id
            WHERE om.user_id = %s
            LIMIT 1
            """,
            (user_id,),
        )
        row = cur.fetchone()
        conn.commit()
        if row is None:
            return None
        return str(row[0]), row[1]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _do_delete_org(user_id: str, confirm_slug: str) -> None:
    """Permanently delete the caller's own org and everything in it.

    Type-to-confirm: confirm_slug must exactly match the org's own slug (as shown
    on GET /account) or nothing is deleted. org_id is resolved from the caller's
    own membership only -- there is no code path where a request parameter can
    target a different org (verified by test_cannot_delete_another_orgs_account).

    Relies entirely on ON DELETE CASCADE (supabase/migrations/20260510000001_
    create_tables.sql and friends) to remove every dependent row -- extractions,
    usage_records, api_keys, batch_jobs, batch_job_rows, corrections, alert
    preferences/log, shopify/google installations, quota_requests. One DELETE,
    one transaction, no partial-delete state possible.
    """
    org = _get_org_id_and_slug(user_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No account found. Complete sign-up first via POST /auth/provision.",
        )
    org_id, actual_slug = org
    if confirm_slug != actual_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirm_slug does not match. Expected the org's slug (see GET /account).",
        )

    conn = _db_connect()
    try:
        cur = conn.cursor()
        log.warning("account.delete_requested", org_id=org_id, slug=actual_slug, user_id=user_id)
        cur.execute("DELETE FROM public.organizations WHERE id = %s", (org_id,))
        deleted = cur.rowcount
        conn.commit()
        if deleted == 0:
            # Org vanished between the lookup above and this DELETE (e.g. a racing
            # duplicate request) -- nothing to do, not an error the caller needs to see.
            log.info("account.delete_no_op_already_gone", org_id=org_id)
        else:
            log.warning("account.deleted", org_id=org_id, slug=actual_slug, user_id=user_id)
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


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    body: DeleteAccountRequest,
    authorization: str = Header(default="", alias="Authorization"),
) -> None:
    """Permanently delete the caller's own org and everything in it.

    Type-to-confirm: body.confirm_slug must exactly match the org's own slug
    (returned by GET /account). Irreversible -- cascades to every table via
    ON DELETE CASCADE. Cannot target any org other than the caller's own; org_id
    is resolved server-side from the verified session, never from the request.
    """
    user_id = await _require_user_id(authorization)
    await asyncio.to_thread(_do_delete_org, user_id, body.confirm_slug)
