"""On-open extraction for reviews the encoder-first router skipped (S29, flag OFF; spec Amendment 14).

A review the encoder judged low-risk is stored classified-only (public.classified_reviews, written by the ingest worker). When its
owner opens it, this runs the full LLM extraction once, links it, and drops the raw text. Nothing here runs unless
`ENABLE_ENCODER_FIRST` is on: the BFF routes answer 404 first, and the ingest worker only stores rows when the flag is on.

Idempotent by design: a second open of an extracted review returns the stored extraction without a model call; two concurrent first
opens may both reach the model once (the extraction cache is not locked), the later UPDATE is a no-op.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from app.auth.api_key import ApiKeyContext
from app.core.schemas import ReviewExtractionV2, ReviewRequest
from app.core.storage_pg import (
    get_by_hash_pg,
    get_classified_review_pg,
    mark_classified_review_extracted_pg,
)

log = structlog.get_logger(__name__)

HASH_PREFIX = "sha256:"
_PREVIEW_CHARS = 160


class ClassifiedReviewNotFoundError(LookupError):
    """No classified-only row for this org and hash (never stored, already purged, or another org's)."""


class ExtractionPurgedError(LookupError):
    """The review was extracted earlier but its extraction (and raw text) have since been purged; nothing is left to show or redo."""


def stored_hash(review_id: str) -> str:
    """Public id (plain sha256 hex, as everywhere in the BFF) -> stored input_hash ('sha256:' + hex)."""
    return review_id if review_id.startswith(HASH_PREFIX) else HASH_PREFIX + review_id


def public_id(input_hash: str) -> str:
    return input_hash.removeprefix(HASH_PREFIX)


def summarize(row: dict[str, Any]) -> dict[str, Any]:
    """List-row shape: id, product, the encoder's four labels, a short text preview. Never the whole text."""
    cls = row.get("classification") or {}
    text = row.get("review_text") or ""
    return {
        "review_id": public_id(row["input_hash"]),
        "product": row.get("product"),
        "encoder": {t: cls[t].get("label") for t in cls},
        "text_preview": text[:_PREVIEW_CHARS] + ("..." if len(text) > _PREVIEW_CHARS else ""),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


async def open_classified_review(ctx: ApiKeyContext, review_id: str) -> ReviewExtractionV2:
    """Extract (once) the classified-only review `review_id` for `ctx.org_id` and return the extraction."""
    h = stored_hash(review_id)
    row = await asyncio.to_thread(get_classified_review_pg, ctx.org_id, h)
    if row is None:
        raise ClassifiedReviewNotFoundError(review_id)
    if row["status"] == "extracted":
        done = await asyncio.to_thread(get_by_hash_pg, ctx.org_id, h)
        if done is None:
            raise ExtractionPurgedError(review_id)
        return done

    from app.api.v2.extract import (
        _run_extraction_v2,
    )  # late import: extract.py imports a lot, and avoids a cycle

    req = ReviewRequest(text=row["review_text"], review_date=row["review_date"])
    extraction = await _run_extraction_v2(req, ctx, product_override=row["product"])
    changed = await asyncio.to_thread(mark_classified_review_extracted_pg, ctx.org_id, h)
    log.info(
        "classified_review.opened", org_id=ctx.org_id, review_id=public_id(h), first_open=changed
    )
    return extraction
