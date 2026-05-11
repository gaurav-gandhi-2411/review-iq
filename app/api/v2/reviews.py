"""GET /v2/reviews and GET /v2/insights endpoints."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.auth.api_key import ApiKeyContext, require_api_key
from app.core.schemas import Sentiment, Urgency
from app.core.storage_pg import aggregate_extractions_pg, list_extractions_pg

router = APIRouter(prefix="/v2", tags=["v2"])


@router.get("/reviews")
async def list_reviews(
    ctx: ApiKeyContext = Depends(require_api_key),
    product: str | None = Query(None, description="Filter by product name (partial match)"),
    sentiment: Sentiment | None = Query(None),
    urgency: Urgency | None = Query(None),
    has_competitor_mention: bool | None = Query(None),
    topic: str | None = Query(None, description="Filter reviews containing this topic"),
    since: datetime | None = Query(None, description="ISO8601 datetime — earliest created_at"),
    until: datetime | None = Query(None, description="ISO8601 datetime — latest created_at"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Query stored review extractions for the authenticated org."""
    rows = await asyncio.to_thread(
        list_extractions_pg,
        ctx.org_id,
        product=product,
        sentiment=sentiment,
        urgency=urgency,
        has_competitor_mention=has_competitor_mention,
        topic=topic,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return {"org_id": ctx.org_id, "count": len(rows), "offset": offset, "limit": limit, "results": rows}


@router.get("/insights")
async def insights(
    ctx: ApiKeyContext = Depends(require_api_key),
) -> dict[str, Any]:
    """Aggregated analytics for the authenticated org."""
    data = await asyncio.to_thread(aggregate_extractions_pg, ctx.org_id)
    return {"org_id": ctx.org_id, **data}
