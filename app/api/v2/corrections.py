from __future__ import annotations

import asyncio
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator, model_validator

from app.auth.api_key import ApiKeyContext, require_api_key
from app.core.corrections.schema import SourceType, validate_field_path
from app.core.corrections.service import list_corrections_pg, submit_correction_pg
from app.core.metrics import CORRECTIONS_SUBMITTED

router = APIRouter(prefix="/v2", tags=["v2"])
log = structlog.get_logger(__name__)


class CorrectionRequest(BaseModel):
    review_id: str
    source_type: SourceType
    field_path: str
    original_value: str
    corrected_value: str
    correction_note: str | None = None
    language: str = "en"

    @field_validator("review_id")
    @classmethod
    def reject_prefixed_review_id(cls, v: str) -> str:
        if v.startswith("sha256:"):
            raise ValueError("review_id must be plain sha256 hex without 'sha256:' prefix")
        return v

    @field_validator("language")
    @classmethod
    def lowercase_language(cls, v: str) -> str:
        return v.lower()

    @model_validator(mode="after")
    def check_field_path(self) -> CorrectionRequest:
        try:
            validate_field_path(self.source_type, self.field_path)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return self


@router.post("/corrections", status_code=status.HTTP_201_CREATED)
async def submit_correction(
    body: CorrectionRequest,
    ctx: Annotated[ApiKeyContext, Depends(require_api_key)],
) -> dict[str, Any]:
    """Submit a correction for a review field that was incorrectly extracted, scored, or replied."""
    try:
        inserted_id = await asyncio.to_thread(
            submit_correction_pg,
            ctx.org_id,
            body.review_id,
            body.source_type.value,
            body.field_path,
            body.original_value,
            body.corrected_value,
            body.correction_note,
            body.language,
        )
    except Exception as exc:
        log.warning("correction.submit_failed", org_id=ctx.org_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store correction.",
        ) from exc
    CORRECTIONS_SUBMITTED.labels(source_type=body.source_type.value).inc()
    log.info(
        "correction.submitted",
        org_id=ctx.org_id,
        source_type=body.source_type.value,
        review_id=body.review_id,
        field_path=body.field_path,
    )
    return {"id": inserted_id, "org_id": ctx.org_id, "review_id": body.review_id}


@router.get("/corrections")
async def list_corrections(
    ctx: Annotated[ApiKeyContext, Depends(require_api_key)],
    source_type: SourceType | None = Query(None),
    review_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List corrections submitted for this org, newest-first."""
    rows = await asyncio.to_thread(
        list_corrections_pg,
        ctx.org_id,
        source_type=source_type.value if source_type else None,
        review_id=review_id,
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
