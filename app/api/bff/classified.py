"""BFF routes for reviews the encoder-first router skipped (S29, flag OFF): list them, open one to extract it.

GET  /bff/classified-reviews                     the org's not-yet-extracted reviews with the encoder's labels
POST /bff/classified-reviews/{review_id}/open    run the full extraction once and return it

Both answer 404 while `ENABLE_ENCODER_FIRST` is off (the routes then look absent). The open route uses `require_session`, so a user-
triggered extraction counts against the key's quota like /bff/reply does.
"""

from __future__ import annotations

import asyncio
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.auth.api_key import ApiKeyContext
from app.auth.session import require_session, require_session_read
from app.core.classified_reviews import (
    ClassifiedReviewNotFoundError,
    ExtractionPurgedError,
    open_classified_review,
    summarize,
)
from app.core.config import get_settings
from app.core.schemas import ReviewExtractionV2
from app.core.storage_pg import list_classified_reviews_pg

router = APIRouter(prefix="/bff", tags=["bff"])

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _require_enabled() -> None:
    if not get_settings().enable_encoder_first:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


@router.get("/classified-reviews")
async def bff_list_classified_reviews(
    ctx: Annotated[ApiKeyContext, Depends(require_session_read)],
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    _require_enabled()
    rows = await asyncio.to_thread(list_classified_reviews_pg, ctx.org_id, limit, offset)
    return {
        "org_id": ctx.org_id,
        "count": len(rows),
        "offset": offset,
        "limit": limit,
        "results": [summarize(r) for r in rows],
    }


@router.post("/classified-reviews/{review_id}/open", response_model=ReviewExtractionV2)
async def bff_open_classified_review(
    ctx: Annotated[ApiKeyContext, Depends(require_session)],
    review_id: Annotated[str, Path(description="Plain sha256 hex, as returned by the list route")],
) -> ReviewExtractionV2:
    _require_enabled()
    if not _HEX64.match(review_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="review_id must be 64 lowercase hex characters",
        )
    try:
        return await open_classified_review(ctx, review_id)
    except ClassifiedReviewNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found") from None
    except ExtractionPurgedError:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This review was extracted earlier and its data has since been purged.",
        ) from None
