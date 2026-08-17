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


_EXAMPLE_DATASET_RECORD = {
    "review_id": "9f2c1a...",
    "review_text": "Great sound quality but the battery dies after 3 hours.",
    "extracted_at": "2026-07-07T12:00:00Z",
    "created_at": "2026-07-07T12:00:00Z",
    "extraction": {
        "product": "wireless headphones",
        "stars": None,
        "stars_inferred": 4,
        "buy_again": True,
        "sentiment": "mixed",
        "urgency": "low",
        "language": "en",
        "review_length_chars": 96,
        "confidence": 0.91,
        "topics": ["sound quality", "battery life"],
        "competitor_mentions": [],
        "pros": ["great sound quality"],
        "cons": ["battery dies after 3 hours"],
        "feature_requests": [],
        "model": "openai/gpt-oss-20b",
        "prompt_version": "2.3",
        "is_suspicious": False,
    },
    "authenticity": {"score": 0.88, "label": "genuine", "flags": []},
    "corrections": [],
}


@router.get(
    "/dataset",
    summary="Structured review dataset, paginated (extraction + authenticity + corrections)",
    openapi_extra={
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": {
                            "org_id": "5b6c1e2a-....",
                            "count": 1,
                            "offset": 0,
                            "limit": 50,
                            "records": [_EXAMPLE_DATASET_RECORD],
                        },
                    },
                },
            },
        },
    },
)
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


@router.get(
    "/dataset/export",
    summary="Export the org's full dataset as newline-delimited JSON",
)
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
