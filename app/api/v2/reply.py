"""POST /v2/reply and POST /v2/reply/batch — vernacular-native reply drafting."""

from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.api_key import ApiKeyContext, require_api_key
from app.core.metrics import REPLY_CACHE_HIT_TOTAL
from app.core.reply.engine import VernacularModelUnavailableError, draft_reply
from app.core.reply.schema import ReplyBatchRequest, ReplyDraft, ReplyRequest
from app.core.storage_pg import update_usage_tokens

router = APIRouter(prefix="/v2", tags=["v2"])
log = structlog.get_logger(__name__)

# In-memory reply cache keyed by "{org_id}:{review_hash+tone+brand+sig}".
# Ephemeral (per-process), suitable for the stateless MVP.
_DRAFT_CACHE: dict[str, ReplyDraft] = {}


async def _run_draft(request: ReplyRequest, ctx: ApiKeyContext) -> ReplyDraft:
    """Core reply drafting pipeline — cache check, LLM call, usage recording."""
    cache_key = f"{ctx.org_id}:{request.cache_key()}"
    cached = _DRAFT_CACHE.get(cache_key)
    if cached is not None:
        log.info("reply.cache_hit", org_id=ctx.org_id)
        REPLY_CACHE_HIT_TOTAL.inc()
        return cached

    draft, tokens_in, tokens_out = await draft_reply(request)

    await asyncio.to_thread(
        update_usage_tokens,
        ctx.usage_record_id,
        tokens_in,
        tokens_out,
    )

    _DRAFT_CACHE[cache_key] = draft
    log.info(
        "reply.drafted",
        org_id=ctx.org_id,
        language=draft.language,
        tone=draft.tone.value,
        model=draft.model_used,
        caveats=draft.caveats,
    )
    return draft


@router.post("/reply", response_model=ReplyDraft)
async def draft_single(
    body: ReplyRequest,
    ctx: ApiKeyContext = Depends(require_api_key),
) -> ReplyDraft:
    """Draft a vernacular-native reply for a single review.

    The reply is written in the same language as the review (en/hi/hi-en) and
    grounded in the structured extraction of that review's cons and topics.
    Drafts are suggestions for human review — never auto-posted.
    """
    try:
        return await _run_draft(body, ctx)
    except VernacularModelUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            headers={"Retry-After": "60"},
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="upstream LLM unavailable",
            headers={"Retry-After": "30"},
        ) from exc


@router.post("/reply/batch", response_model=list[ReplyDraft])
async def draft_batch(
    body: ReplyBatchRequest,
    ctx: ApiKeyContext = Depends(require_api_key),
) -> list[ReplyDraft]:
    """Draft replies for up to 20 reviews (synchronous; same degradation as single).

    Items that fail individually are skipped. A 503 is returned only when every
    item fails (LLM fully unavailable).
    """
    results: list[ReplyDraft] = []
    failed = 0
    for req in body.reviews:
        try:
            results.append(await _run_draft(req, ctx))
        except (RuntimeError, VernacularModelUnavailableError) as exc:
            log.error("reply.batch_item_failed", org_id=ctx.org_id, error=str(exc))
            failed += 1

    if failed == len(body.reviews):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="upstream LLM unavailable for all reviews in batch",
            headers={"Retry-After": "30"},
        )

    log.info(
        "reply.batch_completed",
        org_id=ctx.org_id,
        processed=len(results),
        failed=failed,
    )
    return results
