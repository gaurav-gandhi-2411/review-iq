"""POST /v2/authenticity and POST /v2/authenticity/batch — tenant-scoped authenticity scoring."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth.api_key import ApiKeyContext, require_api_key
from app.core.authenticity import engine
from app.core.config import get_settings
from app.core.storage_pg import save_authenticity_audit_pg

router = APIRouter(prefix="/v2", tags=["v2-authenticity"])
log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class AuthenticityReviewInput(BaseModel):
    """Single review input for authenticity scoring."""

    text: str
    stars: int | None = None


class AuthenticityBatchRequest(BaseModel):
    """Batch of reviews for authenticity scoring.

    Optional ``dates`` list (ISO date strings, YYYY-MM-DD) enables burst
    detection.  Pass ``null`` or omit to skip burst signals.
    """

    reviews: list[AuthenticityReviewInput]
    # Optional: list of ISO date strings (YYYY-MM-DD) for burst detection; None skips burst.
    dates: list[str | None] | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/authenticity")
async def score_authenticity_single(
    body: AuthenticityReviewInput,
    ctx: Annotated[ApiKeyContext, Depends(require_api_key)],
) -> dict[str, object]:
    """Score a single review for authenticity.

    Returns the full ``AuthenticityResult`` serialised as JSON, including
    ``score``, ``label``, ``flags``, ``reasons``, and provenance fields.
    """
    try:
        result = await engine.score_single(body.text, stars=body.stars, settings=get_settings())
    except Exception as exc:
        log.warning(
            "authenticity.engine_error",
            org_id=ctx.org_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authenticity scoring failed.",
        ) from exc

    await asyncio.to_thread(
        save_authenticity_audit_pg,
        ctx.org_id,
        result.review_hash,
        result.score,
        result.label.value,
        [f.value for f in result.flags],
    )

    log.info(
        "authenticity.scored",
        org_id=ctx.org_id,
        label=result.label.value,
        flags=[f.value for f in result.flags],
    )

    return result.model_dump(mode="json")


@router.post("/authenticity/batch")
async def score_authenticity_batch(
    body: AuthenticityBatchRequest,
    ctx: Annotated[ApiKeyContext, Depends(require_api_key)],
) -> dict[str, object]:
    """Score a batch of reviews for authenticity (max 500).

    Accepts an optional ``dates`` list (one per review, YYYY-MM-DD ISO strings)
    to enable burst-detection signals.  Missing dates should be ``null``.
    """
    if len(body.reviews) > 500:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Batch size exceeds 500-row limit.",
        )

    # Parse date strings → datetime | None
    parsed_dates: list[datetime | None] | None
    if body.dates is None:
        parsed_dates = None
    else:
        parsed_dates = [datetime.fromisoformat(d) if d is not None else None for d in body.dates]

    try:
        results = await engine.score_batch(
            [(r.text, r.stars) for r in body.reviews],
            dates=parsed_dates,
            settings=get_settings(),
        )
    except Exception as exc:
        log.warning(
            "authenticity.batch_engine_error",
            org_id=ctx.org_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Batch authenticity scoring failed.",
        ) from exc

    for result in results:
        await asyncio.to_thread(
            save_authenticity_audit_pg,
            ctx.org_id,
            result.review_hash,
            result.score,
            result.label.value,
            [f.value for f in result.flags],
        )

    # Log label summary
    label_counts: dict[str, int] = {}
    for result in results:
        label_counts[result.label.value] = label_counts.get(result.label.value, 0) + 1
    log.info(
        "authenticity.batch_scored",
        org_id=ctx.org_id,
        total=len(results),
        label_counts=label_counts,
    )

    return {
        "total": len(results),
        "results": [r.model_dump(mode="json") for r in results],
    }
