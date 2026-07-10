"""POST /v2/ingest/csv  — bulk CSV review ingestion (tenant-scoped, streaming)."""

from __future__ import annotations

import contextlib
import csv
import io
import json
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse

from app.auth.api_key import ApiKeyContext, require_api_key
from app.core.config import get_settings
from app.core.csv_ingest import (
    CsvColumnError,
    FileTooLargeError,
    RowLimitExceededError,
    read_and_validate_csv,
)
from app.core.ingest_worker import drain_rows
from app.core.storage_pg import (
    count_pending_rows_pg,
    create_batch_job_pg,
    enqueue_batch_job_rows_pg,
    get_batch_job_pg,
    get_by_hash_pg,
    update_batch_job_pg,
)

router = APIRouter(prefix="/v2/ingest", tags=["v2-ingest"])
log = structlog.get_logger(__name__)


def _parse_iso(value: str | None) -> datetime | None:
    """Round-trip a review_date string (already-parsed ISO8601, from read_and_validate_csv) back
    into a datetime for storage_pg -- never re-guesses format, this input is already unambiguous."""
    return datetime.fromisoformat(value) if value else None


# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------


async def _drain_until_job_complete(org_id: str, job_id: str) -> None:
    """Background task: repeatedly drain rows until this job has no pending rows.

    drain_rows() claims pending rows GLOBALLY (oldest-pending-first, any org's
    job) — this loop just keeps calling it until THIS job's own rows are all
    settled. If this instance dies before the loop finishes, POST
    /internal/ingest/tick resumes the remainder on its own schedule — that is
    the durability win; this loop only makes the common case (instance
    survives) finish promptly without waiting on the scheduler.
    """
    import asyncio

    settings = get_settings()
    while await asyncio.to_thread(count_pending_rows_pg, org_id, job_id) > 0:
        await drain_rows(settings.ingest_tick_rows)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/csv",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a CSV of reviews for bulk extraction",
    openapi_extra={
        "responses": {
            "202": {
                "content": {
                    "application/json": {
                        "example": {"job_id": "b1e2c3d4-....", "total": 250, "status": "pending"},
                    },
                },
            },
        },
    },
)
async def ingest_csv(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    ctx: ApiKeyContext = Depends(require_api_key),
    text_column: Annotated[str | None, Form()] = None,
    product_column: Annotated[str | None, Form()] = None,
    date_column: Annotated[str | None, Form()] = None,
    date_format: Annotated[str | None, Form()] = None,
    include_authenticity: Annotated[bool, Form()] = False,
) -> dict[str, object]:
    """Upload a CSV of reviews for bulk extraction.

    Caps (free tier): <= 500 rows, <= 5 MB. Returns job_id immediately.
    Poll GET /v2/ingest/{job_id} for status.

    `date_column`: optional column holding each review's ORIGINAL post date (auto-detected from
    common names if omitted). Unparseable/ambiguous dates are never fabricated -- they're left
    absent, reflected in the returned `date_ambiguous` flag when the whole column's day/month
    convention couldn't be determined. `date_format`: optional "DMY"/"MDY" hint to skip
    auto-detection.
    """
    try:
        rows, resolved_text, resolved_product, resolved_date, date_ambiguous = (
            await read_and_validate_csv(file, text_column, product_column, date_column, date_format)
        )
    except FileTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc
    except RowLimitExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc
    except CsvColumnError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    if not rows:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="CSV contains no non-empty rows in the text column.",
        )

    job_id = str(uuid.uuid4())
    total = len(rows)

    # Store column mapping + include_authenticity now (the worker reads
    # include_authenticity per row); input_hashes are appended on completion.
    initial_meta = json.dumps(
        {
            "text_column": resolved_text,
            "product_column": resolved_product,
            "date_column": resolved_date,
            "date_ambiguous": date_ambiguous,
            "include_authenticity": include_authenticity,
            "input_hashes": [],
        }
    )
    import asyncio

    await asyncio.to_thread(create_batch_job_pg, ctx.org_id, job_id, total, initial_meta)
    # Durable path (Option B, 2026-07-09): rows are persisted in batch_job_rows
    # here, before any processing starts — if this instance dies before the
    # BackgroundTask below finishes, POST /internal/ingest/tick resumes the
    # remainder on a schedule.
    await asyncio.to_thread(
        enqueue_batch_job_rows_pg,
        ctx.org_id,
        job_id,
        [row["text"] for row in rows],
        [row.get("product") for row in rows],
        [_parse_iso(row.get("review_date")) for row in rows],
    )
    await asyncio.to_thread(update_batch_job_pg, ctx.org_id, job_id, status="processing")

    background_tasks.add_task(_drain_until_job_complete, ctx.org_id, job_id)

    log.info(
        "ingest.job_created",
        job_id=job_id,
        total=total,
        org_id=ctx.org_id,
        date_column=resolved_date,
        date_ambiguous=date_ambiguous,
    )
    return {
        "job_id": job_id,
        "total": total,
        "status": "pending",
        "date_column": resolved_date,
        "date_ambiguous": date_ambiguous,
    }


@router.get(
    "/{job_id}",
    summary="Poll the status of a CSV ingest job",
    openapi_extra={
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": {
                            "job_id": "b1e2c3d4-....",
                            "status": "processing",
                            "total": 250,
                            "processed": 180,
                            "failed": 2,
                            "created_at": "2026-07-07T12:00:00Z",
                            "completed_at": None,
                        },
                    },
                },
            },
        },
    },
)
async def get_ingest_status(
    job_id: str,
    ctx: ApiKeyContext = Depends(require_api_key),
) -> dict[str, object]:
    """Poll the status of a CSV ingest job. ``status`` is one of pending, processing, done, failed."""
    import asyncio

    job = await asyncio.to_thread(get_batch_job_pg, ctx.org_id, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "total": job["total"],
        "processed": job["processed"],
        "failed": job["failed"],
        "created_at": str(job["created_at"]),
        "completed_at": str(job["completed_at"]) if job.get("completed_at") else None,
    }


@router.get(
    "/{job_id}/result",
    summary="Download extracted results for a completed CSV ingest job",
    openapi_extra={
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": {
                            "job_id": "b1e2c3d4-....",
                            "total": 1,
                            "results": [{"product": "wireless headphones", "sentiment": "mixed"}],
                        },
                    },
                },
            },
            "409": {
                "content": {
                    "application/json": {
                        "example": {"detail": "Job is not complete yet (status: processing)."},
                    },
                },
            },
        },
    },
)
async def get_ingest_result(
    job_id: str,
    format: str = "json",  # noqa: A002
    ctx: ApiKeyContext = Depends(require_api_key),
) -> object:
    """Download extracted results for a completed ingest job.

    ?format=json (default) — JSON array of extraction objects.
    ?format=csv            — CSV download.
    """
    import asyncio

    job = await asyncio.to_thread(get_batch_job_pg, ctx.org_id, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )
    if job["status"] not in ("done", "failed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job is not complete yet (status: {job['status']}).",
        )

    source_meta: dict[str, object] = {}
    if job.get("source_columns"):
        with contextlib.suppress(json.JSONDecodeError):
            source_meta = json.loads(job["source_columns"])

    input_hashes: list[str] = source_meta.get("input_hashes", [])  # type: ignore[assignment]

    # Fetch extractions for each non-empty hash.
    extractions: list[dict[str, object]] = []
    for ih in input_hashes:
        if not ih:
            continue
        row = await asyncio.to_thread(get_by_hash_pg, ctx.org_id, ih)
        if row is not None:
            extractions.append(row.model_dump(mode="json"))

    if format == "csv":
        if not extractions:
            return StreamingResponse(
                iter([f"job_id\n{job_id}\n"]),
                media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="job_{job_id}.csv"'},
            )

        fieldnames = list(extractions[0].keys())

        def _generate_csv() -> Iterator[str]:
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=fieldnames)
            writer.writeheader()
            yield buf.getvalue()
            for ext in extractions:
                buf = io.StringIO()
                writer = csv.DictWriter(buf, fieldnames=fieldnames)
                # Flatten nested dicts/lists to JSON strings for CSV compatibility.
                flat = {
                    k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in ext.items()
                }
                writer.writerow(flat)
                yield buf.getvalue()

        return StreamingResponse(
            _generate_csv(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="job_{job_id}.csv"'},
        )

    return {"job_id": job_id, "total": len(extractions), "results": extractions}
