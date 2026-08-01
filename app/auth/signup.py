"""Supabase magic-link callback → org + riq_live_ key provisioning."""

from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any

import psycopg2
import psycopg2.errors
import structlog
from fastapi import APIRouter, Header, HTTPException, Request, status

from app.auth.keygen import insert_api_key_with_retry
from app.core.config import get_settings
from app.core.rate_limit import limiter

router = APIRouter(prefix="/auth", tags=["auth"])
log = structlog.get_logger(__name__)


class _ProvisionRaceLost(Exception):
    """Raised internally when organization_members_user_id_key rejects our INSERT --
    another concurrent first-login call for this same user_id already committed its
    own org first. `provision()` catches this and returns the winner's org instead
    of surfacing a 500 (see _provision_org_and_key's docstring)."""


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

    Racy by design: `provision()` does a check-then-act (_get_org_for_user, then
    this) with no lock between them, so two concurrent first calls for the same
    user_id can both reach here. organization_members.user_id carries a UNIQUE
    constraint (see
    supabase/migrations/20260801000001_role_separation_bypassrls_remediation.sql) --
    a product decision that a user belongs to exactly one org, not a storage detail.
    The losing call's membership INSERT raises UniqueViolation; this rolls back its
    own (uncommitted, so never persisted) org + key and raises _ProvisionRaceLost
    for the caller to resolve deterministically against the winner's row.
    """
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

        def _do_insert(raw_key: str, key_prefix: str, key_hash: str) -> None:
            cur.execute(
                """
                INSERT INTO public.api_keys (org_id, key_hash, key_prefix, name, quota)
                VALUES (%s, %s, %s, 'default', 100)
                """,
                (str(org_id), key_hash, key_prefix),
            )

        raw_key, key_prefix, _key_hash = insert_api_key_with_retry(cur, _do_insert)

        try:
            cur.execute(
                "INSERT INTO public.organization_members (org_id, user_id, role) "
                "VALUES (%s, %s, 'owner')",
                (str(org_id), user_id),
            )
        except psycopg2.errors.UniqueViolation as exc:
            diag = getattr(exc, "diag", None)
            if getattr(diag, "constraint_name", None) != "organization_members_user_id_key":
                raise
            raise _ProvisionRaceLost(user_id) from exc

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


@router.post(
    "/provision",
    summary="Issue (or fetch) this user's riq_live_* API key",
    openapi_extra={
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "examples": {
                            "created": {
                                "summary": "First login — key issued",
                                "value": {
                                    "status": "created",
                                    "org_id": "5b6c1e2a-....",
                                    "key_prefix": "riq_live_9f2c1a8b",
                                    "raw_key": "riq_live_9f2c1a8b7d6e5f4a3b2c1d0e9f8a7b6c",
                                    "monthly_quota": 100,
                                },
                            },
                            "existing": {
                                "summary": "Subsequent login — key already issued",
                                "value": {
                                    "status": "existing",
                                    "org_id": "5b6c1e2a-....",
                                    "key_prefix": "riq_live_9f2c1a8b",
                                    "monthly_quota": 100,
                                },
                            },
                        },
                    },
                },
            },
        },
    },
)
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

    try:
        result = await asyncio.to_thread(_provision_org_and_key, str(user.id), user.email or "")
    except _ProvisionRaceLost:
        existing = await asyncio.to_thread(_get_org_for_user, str(user.id))
        if existing is None:
            # Unreachable in practice: the UNIQUE violation means a committed row for
            # this user_id exists by the time our rollback finishes. Surface loudly
            # rather than silently swallow if it ever does happen.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Concurrent signup conflict — retry.",
            ) from None
        return {
            "status": "existing",
            "org_id": existing["org_id"],
            "key_prefix": existing["key_prefix"],
            "monthly_quota": existing["quota"],
        }
    return {"status": "created", **result}
