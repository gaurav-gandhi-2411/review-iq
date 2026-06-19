from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.auth.api_key import ApiKeyContext, require_api_key
from app.core.dataset.builder import get_dataset_page, iter_dataset_jsonl

router = APIRouter(prefix="/v2", tags=["v2"])
log = structlog.get_logger(__name__)


@router.get("/dataset")
async def get_dataset(
    ctx: Annotated[ApiKeyContext, Depends(require_api_key)],
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Return the org's structured review dataset, paginated."""
    try:
        records = await asyncio.to_thread(get_dataset_page, ctx.org_id, limit, offset)
    except Exception as exc:
        log.warning("dataset.fetch_failed", org_id=ctx.org_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch dataset.",
        ) from exc
    log.info("dataset.fetched", org_id=ctx.org_id, count=len(records), offset=offset)
    return {
        "org_id": ctx.org_id,
        "count": len(records),
        "offset": offset,
        "limit": limit,
        "records": records,
    }


@router.get("/dataset/export")
async def export_dataset(
    ctx: Annotated[ApiKeyContext, Depends(require_api_key)],
    format: str = Query("jsonl"),  # noqa: A002
) -> StreamingResponse:
    """Export the org's full dataset as JSONL (one record per line)."""
    if format != "jsonl":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only format=jsonl is supported.",
        )

    def _generate() -> Iterator[str]:
        yield from iter_dataset_jsonl(ctx.org_id)

    log.info("dataset.export_started", org_id=ctx.org_id)
    return StreamingResponse(
        _generate(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="dataset.jsonl"'},
    )
