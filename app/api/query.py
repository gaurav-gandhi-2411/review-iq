"""GET /reviews and GET /insights endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query

from app.core.schemas import Sentiment, Urgency
from app.core.storage import get_insights, query_extractions

router = APIRouter(tags=["query"])


@router.get("/reviews")
async def list_reviews(
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
    """Query stored review extractions with optional filters.

    All filters are AND-combined. Results are ordered by `created_at` descending.
    """
    rows = await query_extractions(
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
    return {"count": len(rows), "offset": offset, "limit": limit, "results": rows}


@router.get("/insights")
async def insights() -> dict[str, Any]:
    """Aggregated analytics: sentiment breakdown, top topics, competitor mentions, urgency volume.

    Data spans all stored extractions.
    """
    return await get_insights()
