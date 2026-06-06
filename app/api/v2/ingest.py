"""POST /v2/ingest/csv  — bulk CSV review ingestion (tenant-scoped, streaming)."""

from __future__ import annotations

import contextlib
import csv
import io
import json
import uuid
from collections.abc import Iterator
from typing import Annotated

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse

from app.auth.api_key import ApiKeyContext, require_api_key
from app.core.csv_ingest import (
    CsvColumnError,
    FileTooLargeError,
    RowLimitExceededError,
    read_and_validate_csv,
)
from app.core.schemas import ReviewRequest
from app.core.storage_pg import (
    create_batch_job_pg,
    get_batch_job_pg,
    get_by_hash_pg,
    update_batch_job_pg,
)

router = APIRouter(prefix="/v2/ingest", tags=["v2-ingest"])
log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------


async def _process_ingest_job(
    ctx: ApiKeyContext,
    job_id: str,
    rows: list[dict[str, str]],
) -> None:
    """Background task: extract each CSV row and track progress in batch_jobs."""
    import asyncio

    from app.api.v2.extract import _run_extraction_v2  # avoid circular at module import time

    await asyncio.to_thread(update_batch_job_pg, ctx.org_id, job_id, status="processing")

    processed = failed = 0
    input_hashes: list[str] = []

    for row in rows:
        try:
            req = ReviewRequest(text=row["text"])
            input_hashes.append(req.input_hash())
            await _run_extraction_v2(req, ctx)
            processed += 1
        except Exception as exc:  # noqa: BLE001
            log.error("ingest.item_failed", job_id=job_id, org_id=ctx.org_id, error=str(exc))
            failed += 1
            input_hashes.append("")  # placeholder keeps index alignment with rows

        await asyncio.to_thread(
            update_batch_job_pg,
            ctx.org_id,
            job_id,
            processed=processed,
            failed=failed,
        )

    # Persist input_hashes so the result endpoint can look up individual extractions.
    source_meta = json.dumps({"input_hashes": input_hashes})
    final_status = "done" if failed == 0 else "failed"
    await asyncio.to_thread(
        update_batch_job_pg,
        ctx.org_id,
        job_id,
        status=final_status,
        source_columns=source_meta,
    )
    log.info(
        "ingest.job_completed",
        job_id=job_id,
        org_id=ctx.org_id,
        processed=processed,
        failed=failed,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/csv", status_code=status.HTTP_202_ACCEPTED)
async def ingest_csv(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    ctx: ApiKeyContext = Depends(require_api_key),
    text_column: Annotated[str | None, Form()] = None,
    product_column: Annotated[str | None, Form()] = None,
) -> dict[str, object]:
    """Upload a CSV of reviews for bulk extraction.

    Caps (free tier): <= 500 rows, <= 5 MB. Returns job_id immediately.
    Poll GET /v2/ingest/{job_id} for status.
    """
    try:
        rows, resolved_text, resolved_product = await read_and_validate_csv(
            file, text_column, product_column
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

    # Store column mapping now; input_hashes are appended on completion.
    initial_meta = json.dumps(
        {
            "text_column": resolved_text,
            "product_column": resolved_product,
            "input_hashes": [],
        }
    )
    import asyncio

    await asyncio.to_thread(create_batch_job_pg, ctx.org_id, job_id, total, initial_meta)

    background_tasks.add_task(_process_ingest_job, ctx, job_id, rows)

    log.info("ingest.job_created", job_id=job_id, total=total, org_id=ctx.org_id)
    return {"job_id": job_id, "total": total, "status": "pending"}


@router.get("/{job_id}")
async def get_ingest_status(
    job_id: str,
    ctx: ApiKeyContext = Depends(require_api_key),
) -> dict[str, object]:
    """Poll the status of a CSV ingest job."""
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


@router.get("/{job_id}/result")
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
