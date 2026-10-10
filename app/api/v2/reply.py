"""POST /v2/reply and POST /v2/reply/batch — vernacular-native reply drafting."""

from __future__ import annotations

import asyncio
import json

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse

from app.auth.api_key import ApiKeyContext, require_api_key
from app.core.config import get_settings
from app.core.metrics import REPLY_CACHE_HIT_TOTAL
from app.core.reply.engine import VernacularModelUnavailableError, draft_reply
from app.core.reply.errors import error_response, item_error_code, unexpected_error_response
from app.core.reply.schema import ReplyBatchRequest, ReplyDraft, ReplyRequest
from app.core.storage_pg import update_usage_tokens

router = APIRouter(prefix="/v2", tags=["v2"])
log = structlog.get_logger(__name__)

# In-memory reply cache keyed by "{org_id}:{review_hash+tone+brand+sig}".
# Ephemeral (per-process), suitable for the stateless MVP.
_DRAFT_CACHE: dict[str, ReplyDraft] = {}


REPLY_DRAFTING_DISABLED_CODE = "reply_drafting_disabled"


def ensure_reply_drafting_enabled() -> None:
    """Raise a typed 503 when the ENABLE_REPLY_DRAFTING kill switch is off.

    Must be called before any cache lookup or provider call. The detail is an object
    ({"code", "message"}) so clients can branch on the machine code instead of parsing prose.
    """
    if get_settings().enable_reply_drafting:
        return
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": REPLY_DRAFTING_DISABLED_CODE,
            "message": "Reply drafting is temporarily unavailable.",
        },
    )


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
        ctx.org_id,
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


_EXAMPLE_REPLY_REQUEST = {
    "text": "Great sound quality but the battery dies after 3 hours.",
    "tone": "apologetic",
    "brand_name": "Acme Audio",
    "signature": "The Acme Audio Team",
}

_EXAMPLE_REPLY_RESPONSE = {
    "reply_text": (
        "Thank you for the kind words about the sound quality! We're sorry to hear the "
        "battery life fell short of your expectations — we're actively working on this. "
        "— The Acme Audio Team"
    ),
    "language": "en",
    "tone": "apologetic",
    "grounded_on": ["great sound quality", "battery dies after 3 hours"],
    "caveats": [],
    "model_used": "llama-3.3-70b-versatile",
    "drafted_at": "2026-07-07T12:00:00Z",
}


@router.post(
    "/reply",
    response_model=ReplyDraft,
    summary="Draft a reply to a single review",
    openapi_extra={
        "requestBody": {
            "content": {"application/json": {"example": _EXAMPLE_REPLY_REQUEST}},
        },
        "responses": {
            "200": {"content": {"application/json": {"example": _EXAMPLE_REPLY_RESPONSE}}},
        },
    },
)
async def draft_single(
    body: ReplyRequest,
    ctx: ApiKeyContext = Depends(require_api_key),
) -> ReplyDraft | JSONResponse:
    """Draft a vernacular-native reply for a single review.

    The reply is written in the same language as the review (en/hi/hi-en) and
    grounded in the structured extraction of that review's cons and topics.
    Drafts are suggestions for human review — never auto-posted.
    """
    ensure_reply_drafting_enabled()
    try:
        return await _run_draft(body, ctx)
    except VernacularModelUnavailableError as exc:
        return error_response(503, "reply_quota_capped", str(exc), retry_after=exc.retry_after)
    except RuntimeError as exc:
        return error_response(
            503,
            "reply_upstream_unavailable",
            "upstream LLM unavailable",
            retry_after=getattr(exc, "retry_after", 30),
        )
    except Exception as exc:  # noqa: BLE001 -- boundary: typed 5xx instead of a bare 500
        return unexpected_error_response(exc, org_id=ctx.org_id)


@router.post(
    "/reply/batch",
    response_model=list[ReplyDraft],
    summary="Draft replies for up to 20 reviews",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {"example": {"reviews": [_EXAMPLE_REPLY_REQUEST]}},
            },
        },
        "responses": {
            "200": {
                "content": {"application/json": {"example": [_EXAMPLE_REPLY_RESPONSE]}},
            },
        },
    },
)
async def draft_batch(
    body: ReplyBatchRequest,
    response: Response,
    ctx: ApiKeyContext = Depends(require_api_key),
) -> list[ReplyDraft] | JSONResponse:
    """Draft replies for up to 20 reviews (synchronous; same degradation as single).

    Items that fail individually are left out of the response list, so list positions no longer
    line up with the request. Every failed item is therefore reported in the `X-Failed-Items`
    header as JSON `[{"index": <position in request.reviews>, "code": <machine code>}]`.
    The body stays a plain list (backward compatible). A 503 (with the same list in `failed_items`)
    is returned only when every item fails.
    """
    ensure_reply_drafting_enabled()
    results: list[ReplyDraft] = []
    failed_items: list[dict[str, int | str]] = []
    retry_after = 30
    for index, req in enumerate(body.reviews):
        try:
            results.append(await _run_draft(req, ctx))
        except Exception as exc:  # noqa: BLE001 -- one bad item must not sink the batch
            code = item_error_code(exc)
            log.error(
                "reply.batch_item_failed",
                org_id=ctx.org_id,
                index=index,
                code=code,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            failed_items.append({"index": index, "code": code})
            retry_after = max(retry_after, getattr(exc, "retry_after", 0))

    if len(failed_items) == len(body.reviews):
        failed = error_response(
            503,
            "reply_batch_all_failed",
            "upstream LLM unavailable for all reviews in batch",
            retry_after=retry_after,
        )
        payload = json.loads(bytes(failed.body))
        payload["failed_items"] = failed_items
        return JSONResponse(status_code=503, content=payload, headers=dict(failed.headers))

    if failed_items:
        response.headers["X-Failed-Items"] = json.dumps(failed_items, separators=(",", ":"))

    log.info(
        "reply.batch_completed",
        org_id=ctx.org_id,
        processed=len(results),
        failed=len(failed_items),
    )
    return results
