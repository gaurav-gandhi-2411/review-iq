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


@router.get(
    "/reviews",
    summary="Query stored review extractions",
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
                            "results": [
                                {
                                    "id": 42,
                                    "input_hash": "sha256:9f2c...",
                                    "review_text": "Great sound quality but the battery "
                                    "dies after 3 hours.",
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
                                    "schema_version": "1.0.0",
                                    "latency_ms": 480,
                                    "extracted_at": "2026-07-07T12:00:00Z",
                                    "created_at": "2026-07-07T12:00:00Z",
                                },
                            ],
                        },
                    },
                },
            },
        },
    },
)
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
    return {
        "org_id": ctx.org_id,
        "count": len(rows),
        "offset": offset,
        "limit": limit,
        "results": rows,
    }


@router.get(
    "/insights",
    summary="Aggregated review analytics for the org",
    openapi_extra={
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": {
                            "org_id": "5b6c1e2a-....",
                            "total_extractions": 128,
                            "sentiment_breakdown": {
                                "positive": 70,
                                "negative": 40,
                                "neutral": 12,
                                "mixed": 6,
                            },
                            "urgency_breakdown": {"low": 100, "medium": 20, "high": 8},
                            "top_topics": [{"topic": "battery life", "count": 34}],
                            "top_competitor_mentions": [],
                        },
                    },
                },
            },
        },
    },
)
async def insights(
    ctx: ApiKeyContext = Depends(require_api_key),
) -> dict[str, Any]:
    """Aggregated analytics for the authenticated org."""
    data = await asyncio.to_thread(aggregate_extractions_pg, ctx.org_id)
    return {"org_id": ctx.org_id, **data}
