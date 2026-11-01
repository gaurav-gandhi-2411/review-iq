"""Typed error contract for the reply endpoints (S19 Q3c).

Shape: `{"detail": <human message>, "code": <machine code>, "correlation_id": <uuid>}` plus an
`X-Correlation-ID` header. `detail` stays a plain string so existing clients (and the web app's
`detail?.detail` read) keep working; `code` and `correlation_id` are additive. The correlation id
is also logged server-side with the real exception, so support can find the cause without the
response ever carrying a stack trace or provider text.
"""

from __future__ import annotations

import uuid

import groq
import structlog
from fastapi.responses import JSONResponse

from app.core.reply.engine import ReplyQuotaExhaustedError, VernacularModelUnavailableError

log = structlog.get_logger(__name__)

# Seconds to wait before retrying a transient transport failure (no provider reset hint exists).
_TRANSIENT_RETRY_AFTER_S = 30


def error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    retry_after: int | None = None,
    correlation_id: str | None = None,
) -> JSONResponse:
    cid = correlation_id or str(uuid.uuid4())
    headers = {"X-Correlation-ID": cid}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(
        status_code=status_code,
        content={"detail": message, "code": code, "correlation_id": cid},
        headers=headers,
    )


def item_error_code(exc: Exception) -> str:
    """Machine code for one failed item of a batch (never carries provider text)."""
    if isinstance(exc, groq.APIConnectionError):
        return "reply_upstream_unreachable"
    if isinstance(exc, groq.APIStatusError):
        return "reply_upstream_error"
    if isinstance(exc, VernacularModelUnavailableError | ReplyQuotaExhaustedError):
        return "reply_quota_capped"
    if isinstance(exc, RuntimeError):
        return "reply_upstream_unavailable"
    return "reply_internal_error"


def unexpected_error_response(exc: Exception, *, org_id: str) -> JSONResponse:
    """Map a non-quota failure that escaped the engine to a typed response (no raw error text)."""
    cid = str(uuid.uuid4())
    log.error(
        "reply.unexpected_error",
        org_id=org_id,
        correlation_id=cid,
        error_type=type(exc).__name__,
        error=str(exc),
        exc_info=exc,
    )
    if isinstance(exc, groq.APIConnectionError):  # includes APITimeoutError
        return error_response(
            503,
            "reply_upstream_unreachable",
            "Reply service could not reach its language model. Please try again shortly.",
            retry_after=_TRANSIENT_RETRY_AFTER_S,
            correlation_id=cid,
        )
    if isinstance(exc, groq.APIStatusError):
        return error_response(
            502,
            "reply_upstream_error",
            "The reply model returned an error. Please try again; if it persists, contact support.",
            correlation_id=cid,
        )
    return error_response(
        500,
        "reply_internal_error",
        "Reply drafting failed unexpectedly. Please try again; if it persists, contact support.",
        correlation_id=cid,
    )
