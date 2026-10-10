"""Postgres backend for the Judge.me connector (tables: supabase/migrations/20261011000001).

Tenant discipline (BYPASSRLS remediation): per-org reads/writes run under ``_set_tenant()``; the
four cross-org / privileged writes go through narrow SECURITY DEFINER functions and are listed in
scripts/check_undocumented_pg_connects.py with the reason. Nothing here is imported by a route or
scheduler yet, and none of it runs unless ENABLE_JUDGEME_CONNECTOR is true.

The reviewer's identity never reaches this module: it only sees ``Mapped`` (text, product, date)
and hashes.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import Any

import psycopg2
import psycopg2.errors

from app.core.ingestion.judgeme_source import Mapped
from app.core.ingestion.judgeme_sync import StateRow
from app.core.storage_pg import (
    _db_connect,
    _set_tenant,
    create_batch_job_pg,
    enqueue_batch_job_rows_pg,
    update_batch_job_pg,
)


class ShopConnectedElsewhere(Exception):
    """The shop is actively connected to a different org."""


def upsert_installation_pg(
    org_id: str, shop_domain: str, token_enc: str, auth_transport: str
) -> str:
    conn = _db_connect()
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT public.upsert_judgeme_installation(%s, %s, %s, %s)",
                (org_id, shop_domain, token_enc, auth_transport),
            )
        except psycopg2.errors.UniqueViolation as exc:
            conn.rollback()
            if "shop_connected_elsewhere" in str(exc):
                raise ShopConnectedElsewhere(shop_domain) from exc
            raise
        row = cur.fetchone()
        conn.commit()
        return str(row[0])
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_sync_pg(
    installation_id: str,
    org_id: str,
    *,
    status: str,
    error: str | None,
    watermark: datetime | None,
    last_full_scan_at: datetime | None,
    order_violation: bool,
    summary: dict[str, Any],
    next_attempt_at: datetime | None,
    failed: bool,
) -> None:
    conn = _db_connect()
    try:
        conn.cursor().execute(
            "SELECT public.record_judgeme_sync(%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)",
            (
                installation_id,
                org_id,
                status,
                error,
                watermark,
                last_full_scan_at,
                order_violation,
                json.dumps(summary),
                next_attempt_at,
                failed,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def revoke_installation_pg(installation_id: str, org_id: str, reason: str) -> None:
    conn = _db_connect()
    try:
        conn.cursor().execute(
            "SELECT public.revoke_judgeme_installation(%s, %s, %s)",
            (installation_id, org_id, reason),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_due_installations_pg(interval_minutes: int) -> list[tuple[str, str]]:
    """[(installation_id, org_id)] due for a run; ids only, never a token."""
    conn = _db_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT installation_id, org_id FROM public.list_due_judgeme_installations(%s)",
            (interval_minutes,),
        )
        rows = cur.fetchall()
        conn.commit()
        return [(str(r[0]), str(r[1])) for r in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_INSTALLATION_COLS = (
    "id, shop_domain, auth_transport, installed_at, revoked_at, revoked_reason, last_sync_at, "
    "last_sync_status, last_sync_error, watermark, last_full_scan_at, order_violation_at"
)


def get_installation_pg(
    org_id: str, installation_id: str | None = None, *, with_token: bool = False
) -> dict[str, Any] | None:
    """The org's most recent installation (or the given one), RLS-scoped to ``org_id``."""
    cols = _INSTALLATION_COLS + (", token_enc" if with_token else "")
    sql = f"SELECT {cols} FROM public.judgeme_installations WHERE org_id = %s"  # noqa: S608 - cols is a constant
    params: list[Any] = [org_id]
    if installation_id:
        sql += " AND id = %s"
        params.append(installation_id)
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(sql + " ORDER BY installed_at DESC LIMIT 1", params)
        row = cur.fetchone()
        names = [d[0] for d in cur.description] if cur.description else []
        conn.commit()
        return dict(zip(names, row, strict=True)) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_state_pg(org_id: str, installation_id: str) -> dict[str, StateRow]:
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            "SELECT review_id, content_hash, input_hash, state, missing_strikes, "
            "source_created_at, source_updated_at FROM public.judgeme_review_state "
            "WHERE org_id = %s AND installation_id = %s",
            (org_id, installation_id),
        )
        rows = cur.fetchall()
        conn.commit()
        return {r[0]: StateRow(r[0], r[1], r[2], r[3], int(r[4]), r[5], r[6]) for r in rows}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_state_pg(org_id: str, installation_id: str, rows: list[StateRow]) -> None:
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.executemany(
            "INSERT INTO public.judgeme_review_state (installation_id, review_id, org_id, "
            "content_hash, input_hash, state, missing_strikes, source_created_at, "
            "source_updated_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now()) "
            "ON CONFLICT (installation_id, review_id) DO UPDATE SET "
            "content_hash = EXCLUDED.content_hash, input_hash = EXCLUDED.input_hash, "
            "state = EXCLUDED.state, missing_strikes = EXCLUDED.missing_strikes, "
            "source_created_at = EXCLUDED.source_created_at, "
            "source_updated_at = EXCLUDED.source_updated_at, updated_at = now()",
            [
                (
                    installation_id,
                    r.review_id,
                    org_id,
                    r.content_hash,
                    r.input_hash,
                    r.state,
                    r.missing_strikes,
                    r.source_created_at,
                    r.source_updated_at,
                )
                for r in rows
            ],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_state_pg(org_id: str, installation_id: str) -> int:
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            "DELETE FROM public.judgeme_review_state WHERE org_id = %s AND installation_id = %s",
            (org_id, installation_id),
        )
        n = cur.rowcount
        conn.commit()
        return n
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def count_tracked_pg(org_id: str, installation_id: str) -> int:
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            "SELECT count(*) FROM public.judgeme_review_state "
            "WHERE org_id = %s AND installation_id = %s AND state = 'active'",
            (org_id, installation_id),
        )
        row = cur.fetchone()
        conn.commit()
        return int(row[0]) if row else 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_extractions_by_hash_pg(org_id: str, input_hashes: list[str]) -> int:
    """Remove superseded/removed reviews' extractions (org-scoped; hashes are text hashes)."""
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            "DELETE FROM public.extractions WHERE org_id = %s AND input_hash = ANY(%s)",
            (org_id, input_hashes),
        )
        n = cur.rowcount
        conn.commit()
        return n
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def stage_rows_pg(org_id: str, rows: list[Mapped]) -> str:
    """Stage reviews on the durable ingest queue exactly like a CSV upload (ingest-tick drains it)."""
    job_id = str(uuid.uuid4())
    meta = json.dumps({"source": "judgeme", "input_hashes": []})
    create_batch_job_pg(org_id, job_id, len(rows), meta)
    enqueue_batch_job_rows_pg(
        org_id,
        job_id,
        [m.text for m in rows],
        [m.product for m in rows],
        [m.created_at for m in rows],
    )
    update_batch_job_pg(org_id, job_id, status="processing")
    return job_id


class PgBackend:
    """SyncStore + Stager + Purger for one installation (blocking calls run in threads)."""

    def __init__(self, org_id: str, installation_id: str) -> None:
        self._org = org_id
        self._inst = installation_id

    async def load_state(self) -> dict[str, StateRow]:
        return await asyncio.to_thread(load_state_pg, self._org, self._inst)

    async def save_state(self, rows: list[StateRow]) -> None:
        await asyncio.to_thread(save_state_pg, self._org, self._inst, rows)

    async def stage(self, rows: list[Mapped]) -> str:
        return await asyncio.to_thread(stage_rows_pg, self._org, rows)

    async def delete_extractions(self, input_hashes: list[str]) -> int:
        return await asyncio.to_thread(delete_extractions_by_hash_pg, self._org, input_hashes)
