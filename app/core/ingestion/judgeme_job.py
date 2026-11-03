"""Scheduled Judge.me sync: one installation per call, plus the due-installation sweep.

Policy (docs/specs/judgeme-ingestion.md 3.2, 3.5), all fail-closed:
  * retention is re-read every run; stateless or an unreadable mode means NO outbound request;
  * a 401/403 is re-issued once, and only a second rejection marks the installation revoked
    (token wiped, polling stops, status endpoint tells the merchant to reconnect) -- never retried
    forever; an unknown shop does the same;
  * 429 beyond the in-run cap defers the installation (next_attempt_at); other failures are
    recorded with a machine code only (never a token, URL or review text) and counted;
  * one installation's failure never stops the sweep, and a timeout mid-run is safe because
    execute is idempotent (stage -> purge -> state).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from app.api.webhooks.shopify import _decrypt_token
from app.core.config import get_settings
from app.core.ingestion.judgeme_client import (
    AuthRejected,
    JudgeMeClient,
    JudgeMeError,
    RateLimited,
    ShopNotFound,
)
from app.core.ingestion.judgeme_store import (
    PgBackend,
    delete_state_pg,
    get_installation_pg,
    list_due_installations_pg,
    record_sync_pg,
    revoke_installation_pg,
)
from app.core.ingestion.judgeme_sync import RunResult, SyncMeta, SyncSettings, run_sync
from app.core.storage_pg import get_org_retention_pg

log = structlog.get_logger(__name__)

PER_INSTALLATION_TIMEOUT_S = 120.0
MAX_INSTALLATIONS_PER_SWEEP = 10
DEFAULT_RATE_LIMIT_DEFER = timedelta(minutes=15)


def make_client(shop_domain: str, token: str, transport: Any) -> JudgeMeClient:
    """Seam for tests."""
    return JudgeMeClient(shop_domain, token, auth_transport=transport)


def sync_settings() -> SyncSettings:
    s = get_settings()
    return SyncSettings(
        order=s.judgeme_list_order,
        overlap=timedelta(hours=s.judgeme_overlap_hours),
        reconcile_interval=timedelta(hours=s.judgeme_reconcile_interval_hours),
        backfill_days=s.judgeme_backfill_days,
        max_enqueue=s.judgeme_max_enqueue_per_run,
    )


async def _record(
    inst_id: str,
    org_id: str,
    status: str,
    *,
    error: str | None = None,
    failed: bool = False,
    next_attempt_at: datetime | None = None,
    summary: dict[str, Any] | None = None,
    meta: SyncMeta | None = None,
    full_scan_at: datetime | None = None,
) -> None:
    await asyncio.to_thread(
        lambda: record_sync_pg(
            inst_id,
            org_id,
            status=status,
            error=error,
            watermark=meta.watermark if meta else None,
            last_full_scan_at=full_scan_at,
            order_violation=bool(meta and meta.order_violation),
            summary=summary or {},
            next_attempt_at=next_attempt_at,
            failed=failed,
        )
    )


async def _run_confirming_auth(
    client: JudgeMeClient, backend: PgBackend, meta: SyncMeta, st: SyncSettings, now: datetime
) -> RunResult:
    try:
        return await run_sync(client, backend, backend, backend, meta, st, now)
    except AuthRejected:
        # Nothing was written before the exception; one immediate re-issue distinguishes a
        # blip from a revoked token without ever looping.
        return await run_sync(client, backend, backend, backend, meta, st, now)


async def sync_installation(
    installation_id: str, org_id: str, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    inst = await asyncio.to_thread(get_installation_pg, org_id, installation_id, with_token=True)
    if inst is None or inst["revoked_at"] is not None:
        return {"installation": "skipped", "reason": "revoked_or_missing"}

    try:
        mode, _days = await asyncio.to_thread(get_org_retention_pg, org_id)
    except Exception:  # noqa: BLE001 - fail closed: cannot prove retained mode, so do nothing
        log.error("judgeme.retention_lookup_failed", org_id=org_id)
        await _record(
            installation_id, org_id, "error", error="retention_lookup_failed", failed=True
        )
        return {"installation": "error", "code": "retention_lookup_failed"}
    if mode != "retained":
        await asyncio.to_thread(delete_state_pg, org_id, installation_id)
        await _record(installation_id, org_id, "skipped_stateless")
        return {"installation": "skipped", "reason": "stateless"}

    try:
        token = _decrypt_token(inst["token_enc"], get_settings().judgeme_token_encryption_key)
    except ValueError:
        await _record(installation_id, org_id, "error", error="token_undecryptable", failed=True)
        return {"installation": "error", "code": "token_undecryptable"}

    meta = SyncMeta(
        inst["watermark"], inst["last_full_scan_at"], inst["order_violation_at"] is not None
    )
    backend = PgBackend(org_id, installation_id)
    try:
        client = make_client(inst["shop_domain"], token, inst["auth_transport"])
        res = await asyncio.wait_for(
            _run_confirming_auth(client, backend, meta, sync_settings(), now),
            PER_INSTALLATION_TIMEOUT_S,
        )
    except (AuthRejected, ShopNotFound) as exc:
        reason = "token_rejected" if isinstance(exc, AuthRejected) else "shop_not_found"
        await asyncio.to_thread(revoke_installation_pg, installation_id, org_id, reason)
        log.warning("judgeme.installation_revoked", org_id=org_id, reason=reason)
        return {"installation": "revoked", "reason": reason}
    except RateLimited as exc:
        defer = timedelta(seconds=exc.retry_after) if exc.retry_after else DEFAULT_RATE_LIMIT_DEFER
        await _record(
            installation_id, org_id, "rate_limited", error=exc.code, next_attempt_at=now + defer
        )
        return {"installation": "deferred", "code": exc.code}
    except TimeoutError:
        await _record(installation_id, org_id, "error", error="sync_timeout", failed=True)
        return {"installation": "error", "code": "sync_timeout"}
    except JudgeMeError as exc:
        await _record(installation_id, org_id, "error", error=exc.code, failed=True)
        return {"installation": "error", "code": exc.code}
    except Exception:  # noqa: BLE001 - one installation must never stop the sweep
        log.exception("judgeme.sync_unexpected_error", org_id=org_id)
        await _record(installation_id, org_id, "error", error="internal_error", failed=True)
        return {"installation": "error", "code": "internal_error"}

    summary = {**res.plan.counts, "mode": res.mode, "pages": res.pages}
    await _record(
        installation_id,
        org_id,
        "suspect" if res.suspect else "ok",
        summary=summary,
        meta=res.meta,
        full_scan_at=res.meta.last_full_scan_at,
    )
    return {"installation": "ok", "suspect": res.suspect, **summary}


async def run_due_installations(now: datetime | None = None) -> dict[str, Any]:
    s = get_settings()
    if not s.enable_judgeme_connector:
        return {"enabled": False}
    due = await asyncio.to_thread(list_due_installations_pg, s.judgeme_sync_interval_minutes)
    out: dict[str, Any] = {"enabled": True, "due": len(due), "ran": 0, "revoked": 0, "errors": 0}
    for inst_id, org_id in due[:MAX_INSTALLATIONS_PER_SWEEP]:
        result = await sync_installation(inst_id, org_id, now)
        out["ran"] += 1
        out["revoked"] += result["installation"] == "revoked"
        out["errors"] += result["installation"] == "error"
    log.info("judgeme.sweep_completed", **out)
    return out
