"""POST /v2/authenticity and POST /v2/authenticity/batch — tenant-scoped authenticity scoring."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth.api_key import ApiKeyContext, require_api_key
from app.core.authenticity import engine
from app.core.authenticity.schema import AuthenticityFlag, AuthenticityLabel, AuthenticityResult
from app.core.config import get_settings
from app.core.storage_pg import get_authenticity_audit_by_hash_pg, save_authenticity_audit_pg

router = APIRouter(prefix="/v2", tags=["v2-authenticity"])
log = structlog.get_logger(__name__)


def _review_hash(text: str) -> str:
    """Return sha256 hex digest of raw review text — matches how audits are stored."""
    return hashlib.sha256(text.encode()).hexdigest()


def _audit_row_to_result(row: dict[str, object], review_text: str) -> AuthenticityResult:
    """Reconstruct a minimal AuthenticityResult from a stored authenticity_audits row.

    The stored row has ``score``, ``label``, ``flags``, ``review_hash``.
    ``reasons``, ``model_used``, and ``scored_at`` are set to sentinel values
    because they are not persisted — the response shape is unchanged.
    """
    raw_flags: list[str] = row["flags"]  # type: ignore[assignment]
    parsed_flags: list[AuthenticityFlag] = []
    for f in raw_flags:
        with contextlib.suppress(ValueError):  # ignore unknown flags stored before a schema update
            parsed_flags.append(AuthenticityFlag(f))

    raw_label = str(row["label"])
    try:
        label = AuthenticityLabel(raw_label)
    except ValueError:
        label = AuthenticityLabel.GENUINE

    return AuthenticityResult(
        score=float(row["score"]),  # type: ignore[arg-type]
        label=label,
        flags=parsed_flags,
        reasons="",  # not persisted; omitted on cache-served response
        review_hash=str(row["review_hash"]),
        scored_at=datetime.now(UTC),  # wall-clock of this request, not original scoring time
        model_used=None,  # not persisted
        llm_signal_ok=False,  # not persisted; conservative default
    )


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

    Pre-LLM short-circuit: if an audit already exists in ``authenticity_audits``
    for (org_id, review_hash), the stored result is returned without re-calling
    the LLM — saving tokens on repeated identical review text.
    """
    rh = _review_hash(body.text)

    # Pre-LLM cache short-circuit. A cache-lookup failure must NEVER fail the request —
    # the cache only saves tokens, it is not load-bearing for correctness. Degrade to a
    # cache miss and score normally.
    try:
        existing = await asyncio.to_thread(get_authenticity_audit_by_hash_pg, ctx.org_id, rh)
    except Exception as exc:  # noqa: BLE001
        log.warning("authenticity.cache_lookup_failed", org_id=ctx.org_id, error=str(exc))
        existing = None
    if existing is not None:
        log.info("authenticity.cache_hit", org_id=ctx.org_id, review_hash=rh[:16])
        result = _audit_row_to_result(existing, body.text)
        return result.model_dump(mode="json")

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
