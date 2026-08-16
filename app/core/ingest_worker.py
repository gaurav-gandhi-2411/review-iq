"""Tick-worker drain logic for the durable bulk-ingest queue (batch_job_rows).

Option B of the 2026-07-07 CSV-throttling fix. Option A (app/core/ratelimit.py)
throttles bulk Groq calls at the call layer, in-process; this module adds
durability so bulk rows survive a Cloud Run restart/scale-down: rows are
persisted at submit time (storage_pg.enqueue_batch_job_rows_pg) and drained a
few at a time by drain_rows(), called either directly from the submitting
endpoint's own BackgroundTask (fast path — same-instance completion, no wait
on a schedule) or from POST /internal/ingest/tick on a Cloud Scheduler cadence
(recovery path — resumes rows an instance died before finishing).

SYSTEM PATH WARNING — cross-tenant risk: drain_rows() claims pending rows
across ALL orgs on one shared service-role connection; RLS does not bind it
(the claim query never calls storage_pg._set_tenant). Every extraction it
performs MUST be attributed to THAT ROW's own org_id via a fresh ApiKeyContext
built per row — never a caller's ctx, never a default org. This is the same
cross-tenant-write risk class as the Shopify webhook org lookup
(app/api/webhooks/shopify.py) — a mistake here leaks one tenant's review text
and results into another tenant's data.

Claim discipline — one short transaction per row: BEGIN; SELECT ... FOR UPDATE
SKIP LOCKED (claim one pending row, none -> commit + stop); process the row —
the extraction call happens INSIDE this transaction window, a deliberate
deviation from "never hold a transaction across a network call" (see
_claim_one_row's docstring for why); UPDATE the row's terminal status; COMMIT.
Holding the lock across processing is what makes concurrent drain_rows()
callers unable to double-process the same row.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import psycopg2
import structlog

from app.auth.api_key import ApiKeyContext
from app.core.alerts.engine import alert_on_review_event
from app.core.config import get_settings
from app.core.ratelimit import set_bulk_call_class
from app.core.schemas import ReviewRequest
from app.core.storage_pg import (
    count_job_row_statuses_pg,
    count_pending_rows_pg,
    get_batch_job_pg,
    list_job_row_hashes_pg,
    update_batch_job_pg,
)

log = structlog.get_logger(__name__)

_ERROR_TRUNCATE_LEN = 500
# System context name for worker-triggered extractions — mirrors the existing
# webhook convention (e.g. key_name="shopify_webhook" in app/api/webhooks/shopify.py).
_SYSTEM_KEY_NAME = "ingest_tick_worker"


async def _claim_one_row() -> tuple[str, str, bool] | None:
    """Claim, process, and settle exactly one pending row inside one short transaction.

    The extraction call runs INSIDE the transaction window that holds the row's
    FOR UPDATE lock — a conscious deviation from the "never hold a transaction
    across a network call" default. The window is bounded by the extraction
    pipeline's own timeout (LLM_TIMEOUT_SECONDS, a few seconds at most), and
    holding the lock across it is exactly what makes concurrent drain_rows()
    callers (a scheduled tick overlapping a job's own completion loop, or two
    overlapping ticks) structurally unable to double-process the same row —
    releasing the lock before processing would reopen that race.

    Claim and settle both go through narrow SECURITY DEFINER functions
    (BYPASSRLS remediation 2c) -- this claim must see pending rows across ALL
    orgs, which no per-org RLS policy expression can do; see
    public.claim_pending_batch_job_row()'s own migration comment for why a
    _set_tenant()-based fix does not work here. Both calls run on this same
    connection/transaction, so the claim's row lock is held until settle
    commits, exactly as it was when this was a single raw SELECT ... UPDATE.

    Returns (org_id, job_id, ok) for the row that was claimed, or None if no
    row was pending.
    """
    settings = get_settings()
    conn = await asyncio.to_thread(psycopg2.connect, settings.supabase_database_url)
    try:
        cur = conn.cursor()
        await asyncio.to_thread(cur.execute, "SELECT * FROM public.claim_pending_batch_job_row()")
        row = await asyncio.to_thread(cur.fetchone)
        if row is None:
            await asyncio.to_thread(conn.commit)
            return None

        job_id, row_index, org_id_raw, text, product, review_date = row
        org_id = str(org_id_raw)

        include_authenticity = False
        job = await asyncio.to_thread(get_batch_job_pg, org_id, job_id)
        if job and job.get("source_columns"):
            with contextlib.suppress(json.JSONDecodeError):
                meta = json.loads(job["source_columns"])
                include_authenticity = bool(meta.get("include_authenticity", False))

        # Row-level ctx: attribute this extraction to THIS ROW's org_id only —
        # never the caller's org, never a default. api_key_id=None +
        # usage_record_id="" mirrors the existing system-triggered-extraction
        # convention used by the Shopify/Google webhook handlers.
        ctx = ApiKeyContext(
            org_id=org_id, api_key_id=None, key_name=_SYSTEM_KEY_NAME, usage_record_id=""
        )

        from app.api.v2.extract import _run_extraction_v2  # late import — avoid circular

        input_hash = ""
        error: str | None = None
        ok = True
        try:
            req = ReviewRequest(text=text, review_date=review_date)
            input_hash = req.input_hash()
            await _run_extraction_v2(req, ctx, product_override=product)
        except Exception as exc:  # noqa: BLE001 — one row's failure must not kill the drain loop
            ok = False
            error = str(exc)[:_ERROR_TRUNCATE_LEN]
            log.error(
                "ingest_worker.row_failed",
                job_id=job_id,
                org_id=org_id,
                row_index=row_index,
                error=error,
            )

        if ok and include_authenticity:
            await _score_authenticity(ctx, job_id, text)

        await asyncio.to_thread(
            cur.execute,
            "SELECT public.settle_batch_job_row(%s, %s, %s, %s, %s)",
            (job_id, row_index, "done" if ok else "failed", error, input_hash or None),
        )
        await asyncio.to_thread(conn.commit)
        return (org_id, job_id, ok)
    except Exception:
        await asyncio.to_thread(conn.rollback)
        raise
    finally:
        await asyncio.to_thread(conn.close)


async def _score_authenticity(ctx: ApiKeyContext, job_id: str, text: str) -> None:
    """Run authenticity scoring for one row — behavior carried over from the
    retired fire-and-forget ingest path (app.api.v2.ingest._process_ingest_job,
    deleted 2026-07-09 once the BFF endpoint moved to this queue).

    Best-effort: a scoring failure is logged and swallowed, never affects the
    row's extraction outcome (the extraction already succeeded by the time
    this is called).
    """
    from app.core.authenticity import engine as auth_engine
    from app.core.storage_pg import save_authenticity_audit_pg

    try:
        auth_result = await auth_engine.score_single(text, stars=None, settings=get_settings())
        await asyncio.to_thread(
            save_authenticity_audit_pg,
            ctx.org_id,
            auth_result.review_hash,
            auth_result.score,
            auth_result.label.value,
            [f.value for f in auth_result.flags],
        )
        await alert_on_review_event(
            org_id=ctx.org_id, review_id=auth_result.review_hash, auth=auth_result
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("ingest_worker.authenticity_failed", job_id=job_id, error=str(exc))


async def _sync_job_progress(org_id: str, job_id: str) -> bool:
    """Recompute a job's processed/failed counts from batch_job_rows and write them.

    If the job has zero pending rows left, also finalizes status (done/failed)
    and rewrites source_columns with the final input_hashes list — preserving
    the existing GET /v2/ingest/{job_id}/result contract shape byte-for-byte.

    Returns True if this call performed job completion (status -> done/failed).
    """
    done_count, failed_count = await asyncio.to_thread(count_job_row_statuses_pg, org_id, job_id)
    pending = await asyncio.to_thread(count_pending_rows_pg, org_id, job_id)

    if pending > 0:
        await asyncio.to_thread(
            update_batch_job_pg, org_id, job_id, processed=done_count, failed=failed_count
        )
        return False

    job = await asyncio.to_thread(get_batch_job_pg, org_id, job_id)
    if job is None or job["status"] in ("done", "failed"):
        return False  # already finalized by a concurrent drain — avoid double completion

    existing_meta: dict[str, object] = {}
    if job.get("source_columns"):
        with contextlib.suppress(json.JSONDecodeError):
            existing_meta = json.loads(job["source_columns"])
    existing_meta["input_hashes"] = await asyncio.to_thread(list_job_row_hashes_pg, org_id, job_id)

    final_status = "done" if failed_count == 0 else "failed"
    await asyncio.to_thread(
        update_batch_job_pg,
        org_id,
        job_id,
        processed=done_count,
        failed=failed_count,
        status=final_status,
        source_columns=json.dumps(existing_meta),
    )
    log.info(
        "ingest_worker.job_completed",
        job_id=job_id,
        org_id=org_id,
        processed=done_count,
        failed=failed_count,
        status=final_status,
    )
    return True


async def drain_rows(max_rows: int) -> dict[str, object]:
    """Claim and process up to `max_rows` pending rows, across all orgs.

    Belt-and-braces: classifies this coroutine's call tree as bulk via
    set_bulk_call_class() so the Option A limiter (app/core/ratelimit.py)
    still bounds Groq call rate even under this worker path — safe to call
    repeatedly, whether from POST /internal/ingest/tick or a submitting
    endpoint's own BackgroundTask (both paths land here).

    Args:
        max_rows: Upper bound on rows claimed in this call. Stops early if the
            queue empties before reaching this bound.

    Returns:
        {"claimed": int, "processed": int, "failed": int, "jobs_completed": list[str]}
    """
    set_bulk_call_class()

    claimed = processed = failed = 0
    touched_jobs: set[tuple[str, str]] = set()

    for _ in range(max_rows):
        result = await _claim_one_row()
        if result is None:
            break
        org_id, job_id, ok = result
        claimed += 1
        if ok:
            processed += 1
        else:
            failed += 1
        touched_jobs.add((org_id, job_id))

    jobs_completed: list[str] = []
    for org_id, job_id in touched_jobs:
        if await _sync_job_progress(org_id, job_id):
            jobs_completed.append(job_id)

    return {
        "claimed": claimed,
        "processed": processed,
        "failed": failed,
        "jobs_completed": jobs_completed,
    }
