"""Admin endpoints: organization and API key management.

All routes require HTTP Basic auth via require_admin.
Raw API keys are returned exactly once (on creation/rotation) and never stored.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Literal

import psycopg2
import psycopg2.errors
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth.admin import require_admin
from app.auth.keygen import generate_api_key
from app.core.config import get_settings

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CreateOrgRequest(BaseModel):
    name: str
    slug: str
    plan: Literal["free", "pro", "enterprise"] = "free"


class OrgOut(BaseModel):
    id: str
    name: str
    slug: str
    plan: str
    created_at: datetime


class CreateKeyRequest(BaseModel):
    name: str
    quota: int = 1000


class KeyOut(BaseModel):
    id: str
    name: str
    key_prefix: str
    quota: int
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class CreateKeyOut(BaseModel):
    id: str
    raw_key: str
    key_prefix: str
    name: str
    quota: int
    created_at: datetime
    note: str = "Store this key securely — it will not be shown again."


class ListKeysOut(BaseModel):
    keys: list[KeyOut]


# ---------------------------------------------------------------------------
# DB helpers (sync, run via asyncio.to_thread)
# ---------------------------------------------------------------------------


def _db_connect() -> psycopg2.extensions.connection:
    settings = get_settings()
    # Pooler (transaction mode) for app traffic. Direct URL only used by
    # migrations and RLS tests that need session-level GUCs.
    return psycopg2.connect(settings.supabase_database_url)


def _create_org_db(name: str, slug: str, plan: str) -> OrgOut:
    conn = _db_connect()
    conn.autocommit = False
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO public.organizations (name, slug, plan) "
            "VALUES (%s, %s, %s) RETURNING id, name, slug, plan, created_at",
            (name, slug, plan),
        )
        row = cur.fetchone()
        conn.commit()
        return OrgOut(id=str(row[0]), name=row[1], slug=row[2], plan=row[3], created_at=row[4])
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Slug '{slug}' already in use.",
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _get_org_db(org_id: str) -> OrgOut:
    conn = _db_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, slug, plan, created_at "
            "FROM public.organizations WHERE id = %s",
            (org_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Organization not found.",
            )
        return OrgOut(id=str(row[0]), name=row[1], slug=row[2], plan=row[3], created_at=row[4])
    finally:
        conn.close()


def _create_key_db(org_id: str, name: str, quota: int) -> CreateKeyOut:
    # Generate key before opening the connection — argon2 takes ~100ms.
    raw_key, key_prefix, key_hash = generate_api_key()

    conn = _db_connect()
    conn.autocommit = False
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM public.organizations WHERE id = %s", (org_id,))
        if cur.fetchone() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Organization not found.",
            )
        cur.execute(
            "INSERT INTO public.api_keys (org_id, key_prefix, key_hash, name, quota) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id, created_at",
            (org_id, key_prefix, key_hash, name, quota),
        )
        row = cur.fetchone()
        conn.commit()
        return CreateKeyOut(
            id=str(row[0]),
            raw_key=raw_key,
            key_prefix=key_prefix,
            name=name,
            quota=quota,
            created_at=row[1],
        )
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _list_keys_db(org_id: str) -> list[KeyOut]:
    conn = _db_connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM public.organizations WHERE id = %s", (org_id,))
        if cur.fetchone() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Organization not found.",
            )
        cur.execute(
            "SELECT id, name, key_prefix, quota, created_at, last_used_at, revoked_at "
            "FROM public.api_keys WHERE org_id = %s ORDER BY created_at DESC",
            (org_id,),
        )
        rows = cur.fetchall()
        return [
            KeyOut(
                id=str(r[0]),
                name=r[1],
                key_prefix=r[2],
                quota=r[3],
                created_at=r[4],
                last_used_at=r[5],
                revoked_at=r[6],
            )
            for r in rows
        ]
    finally:
        conn.close()


def _rotate_key_db(org_id: str, key_id: str) -> CreateKeyOut:
    # Generate new key before opening connection.
    raw_key, key_prefix, key_hash = generate_api_key()

    conn = _db_connect()
    conn.autocommit = False
    try:
        cur = conn.cursor()
        # Lock old key row; verify it belongs to org and is not already revoked.
        cur.execute(
            "SELECT id FROM public.api_keys "
            "WHERE id = %s AND org_id = %s AND revoked_at IS NULL FOR UPDATE",
            (key_id, org_id),
        )
        if cur.fetchone() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Key not found or already revoked.",
            )
        cur.execute(
            "UPDATE public.api_keys SET revoked_at = now() WHERE id = %s",
            (key_id,),
        )
        # Fetch quota and name from old key to carry forward to new key.
        cur.execute(
            "SELECT name, quota FROM public.api_keys WHERE id = %s",
            (key_id,),
        )
        old_name, old_quota = cur.fetchone()
        cur.execute(
            "INSERT INTO public.api_keys (org_id, key_prefix, key_hash, name, quota) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id, created_at",
            (org_id, key_prefix, key_hash, old_name, old_quota),
        )
        row = cur.fetchone()
        conn.commit()
        return CreateKeyOut(
            id=str(row[0]),
            raw_key=raw_key,
            key_prefix=key_prefix,
            name=old_name,
            quota=old_quota,
            created_at=row[1],
        )
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _revoke_key_db(org_id: str, key_id: str) -> None:
    conn = _db_connect()
    conn.autocommit = False
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE public.api_keys SET revoked_at = now() "
            "WHERE id = %s AND org_id = %s AND revoked_at IS NULL RETURNING id",
            (key_id, org_id),
        )
        if cur.fetchone() is None:
            conn.rollback()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Key not found or already revoked.",
            )
        conn.commit()
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/organizations", status_code=status.HTTP_201_CREATED)
async def create_organization(
    body: CreateOrgRequest,
    _: None = Depends(require_admin),
) -> OrgOut:
    return await asyncio.to_thread(_create_org_db, body.name, body.slug, body.plan)


@router.get("/organizations/{org_id}")
async def get_organization(
    org_id: uuid.UUID,
    _: None = Depends(require_admin),
) -> OrgOut:
    return await asyncio.to_thread(_get_org_db, str(org_id))


@router.post("/organizations/{org_id}/keys", status_code=status.HTTP_201_CREATED)
async def create_api_key(
    org_id: uuid.UUID,
    body: CreateKeyRequest,
    _: None = Depends(require_admin),
) -> CreateKeyOut:
    return await asyncio.to_thread(_create_key_db, str(org_id), body.name, body.quota)


@router.get("/organizations/{org_id}/keys")
async def list_api_keys(
    org_id: uuid.UUID,
    _: None = Depends(require_admin),
) -> ListKeysOut:
    keys = await asyncio.to_thread(_list_keys_db, str(org_id))
    return ListKeysOut(keys=keys)


@router.post(
    "/organizations/{org_id}/keys/{key_id}/rotate",
    status_code=status.HTTP_201_CREATED,
)
async def rotate_api_key(
    org_id: uuid.UUID,
    key_id: uuid.UUID,
    _: None = Depends(require_admin),
) -> CreateKeyOut:
    return await asyncio.to_thread(_rotate_key_db, str(org_id), str(key_id))


@router.delete("/organizations/{org_id}/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    org_id: uuid.UUID,
    key_id: uuid.UUID,
    _: None = Depends(require_admin),
) -> None:
    await asyncio.to_thread(_revoke_key_db, str(org_id), str(key_id))
