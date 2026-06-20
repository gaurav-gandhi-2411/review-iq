"""BFF (Backend-for-Frontend) router — browser auth path.

All routes use require_session (Supabase JWT) instead of require_api_key.
Business logic is delegated to the same storage/core functions as the v2
endpoints.  This file MUST NOT import from app.api.v2.* route modules
(which contain HTTP handler boilerplate), with one exception noted below.

Security invariants enforced by structure (verified by test_bff_session.py):
  - Raw API key material must not appear in this file
  - Stored hash field must not appear in this file
  - Internal key/record IDs are never echoed in response payloads
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import structlog
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import BaseModel, field_validator, model_validator

# _process_ingest_job is defined in app.api.v2.ingest because it depends on
# _run_extraction_v2 which lives there too.  It is a pure business-logic
# coroutine and has no coupling to the HTTP handler — importing it here is
# safe and avoids duplication.
from app.api.v2.ingest import _process_ingest_job
from app.auth.api_key import ApiKeyContext
from app.auth.session import require_session, require_session_read
from app.core.authenticity import engine
from app.core.authenticity.schema import AuthenticityFlag, AuthenticityLabel, AuthenticityResult
from app.core.config import get_settings
from app.core.corrections.schema import SourceType, validate_field_path
from app.core.corrections.service import list_corrections_pg, submit_correction_pg
from app.core.csv_ingest import (
    CsvColumnError,
    FileTooLargeError,
    RowLimitExceededError,
    read_and_validate_csv,
)
from app.core.dataset.builder import get_dataset_page
from app.core.metrics import CORRECTIONS_SUBMITTED, REPLY_CACHE_HIT_TOTAL
from app.core.reply.engine import VernacularModelUnavailableError, draft_reply
from app.core.reply.schema import ReplyDraft, ReplyRequest
from app.core.schemas import Sentiment, Urgency
from app.core.storage_pg import (
    authenticity_audit_summary_pg,
    create_batch_job_pg,
    get_authenticity_audit_by_hash_pg,
    get_batch_job_pg,
    health_score_pg,
    list_extractions_pg,
    record_quota_request_pg,
    save_authenticity_audit_pg,
    theme_trends_pg,
    update_usage_tokens,
)

router = APIRouter(prefix="/bff", tags=["bff"])
log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Shared constants copied from v2 endpoints (kept here for colocation)
# ---------------------------------------------------------------------------

DISPOSITION_DISPLAY: dict[str, str] = {
    "genuine": "clear",
    "suspicious": "flagged_for_review",
    "likely_fake": "priority_review",
}

SIGNAL_DISPLAY: dict[str, str] = {
    "incentivized_phrase": "disclosed_incentive",
    "rating_text_mismatch": "rating_text_mismatch",
    "generic_low_info": "low_information",
    "excessive_brevity": "very_short",
    "promotional_tone": "promotional_tone",
    "near_duplicate": "near_duplicate",
    "review_burst": "burst_pattern",
    "repetitive_content": "templated_pattern",
}

_VALID_BUCKETS = frozenset({"day", "week", "month"})
_VALID_TREND_OF = frozenset({"topics", "cons"})

_MODERATION_NOTE = (
    "Signals support human moderation under IS 19000:2022; "
    "dispositions are review priorities for an administrator, not verdicts."
)
_HS_NOTE = (
    "Health score is an org-level aggregate and does not label or score any individual review. "
    "Authenticity signals support human moderation under IS 19000:2022."
)

_FORMULA_VERSION = "1.0"
_W_S: float = 0.50
_W_U: float = 0.20
_W_A: float = 0.30
_BAND_HEALTHY: float = 0.75
_BAND_NEEDS_ATTENTION: float = 0.50
_CONFIDENCE_HIGH: int = 50
_CONFIDENCE_MEDIUM: int = 10

# Per-process reply cache — same ephemeral approach as v2/reply.py.
_DRAFT_CACHE: dict[str, ReplyDraft] = {}


# ---------------------------------------------------------------------------
# Request models (copied from v2 endpoints — not imported to avoid
# coupling to HTTP handler boilerplate)
# ---------------------------------------------------------------------------


class AuthenticityReviewInput(BaseModel):
    text: str
    stars: int | None = None


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


# ---------------------------------------------------------------------------
# Helpers (ported from insights.py)
# ---------------------------------------------------------------------------


def _map_disposition(raw: str) -> str:
    return DISPOSITION_DISPLAY.get(raw, "review")


def _map_signal(raw: str) -> str:
    return SIGNAL_DISPLAY.get(raw, "other_signal")


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 6)


def _assign_band(score: float) -> str:
    if score >= _BAND_HEALTHY:
        return "healthy"
    if score >= _BAND_NEEDS_ATTENTION:
        return "needs_attention"
    return "at_risk"


def _assign_confidence(total_extractions: int) -> str:
    if total_extractions >= _CONFIDENCE_HIGH:
        return "high"
    if total_extractions >= _CONFIDENCE_MEDIUM:
        return "medium"
    return "low"


def _compute_delta(series: list[dict[str, Any]]) -> tuple[int, float | None]:
    if len(series) < 2:
        return 0, None
    latest = series[-1]["count"]
    prior = series[-2]["count"]
    delta = latest - prior
    pct: float | None = None if prior == 0 else round((latest - prior) / prior, 6)
    return delta, pct


def _review_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _audit_row_to_result(row: dict[str, object]) -> AuthenticityResult:
    raw_flags: list[str] = row["flags"]  # type: ignore[assignment]
    parsed_flags: list[AuthenticityFlag] = []
    for f in raw_flags:
        with contextlib.suppress(ValueError):
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
        reasons="",
        review_hash=str(row["review_hash"]),
        scored_at=datetime.now(UTC),
        model_used=None,
        llm_signal_ok=False,
    )


# ---------------------------------------------------------------------------
# Account helpers (DB calls scoped to the caller's own api_key_id / org_id)
# ---------------------------------------------------------------------------


def _get_quota_and_usage(api_key_id: str, org_id: str) -> tuple[int, int]:  # noqa: ARG001
    """Return (quota, monthly_usage_count) for the authenticated key."""
    import psycopg2 as _psycopg2

    from app.core.config import get_settings as _gs

    conn = _psycopg2.connect(_gs().supabase_database_url)
    try:
        cur = conn.cursor()
        cur.execute("SELECT quota FROM public.api_keys WHERE id = %s", (api_key_id,))
        row = cur.fetchone()
        quota: int = int(row[0]) if row else 0

        cur.execute(
            "SELECT COUNT(*) FROM public.usage_records "
            "WHERE api_key_id = %s "
            "AND date_trunc('month', created_at) = date_trunc('month', now())",
            (api_key_id,),
        )
        (monthly_count,) = cur.fetchone()
        conn.commit()
        return quota, int(monthly_count)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/reviews")
async def bff_list_reviews(
    ctx: Annotated[ApiKeyContext, Depends(require_session_read)],
    product: str | None = Query(None),
    sentiment: Sentiment | None = Query(None),
    urgency: Urgency | None = Query(None),
    has_competitor_mention: bool | None = Query(None),
    topic: str | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List stored review extractions for the authenticated org."""
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


@router.post("/authenticity")
async def bff_score_authenticity(
    body: AuthenticityReviewInput,
    ctx: Annotated[ApiKeyContext, Depends(require_session)],
) -> dict[str, object]:
    """Score a single review for authenticity (BFF path)."""
    rh = _review_hash(body.text)

    try:
        existing = await asyncio.to_thread(get_authenticity_audit_by_hash_pg, ctx.org_id, rh)
    except Exception as exc:  # noqa: BLE001
        log.warning("bff.authenticity.cache_lookup_failed", org_id=ctx.org_id, error=str(exc))
        existing = None

    if existing is not None:
        log.info("bff.authenticity.cache_hit", org_id=ctx.org_id, review_hash=rh[:16])
        result = _audit_row_to_result(existing)
        return result.model_dump(mode="json")

    try:
        result = await engine.score_single(body.text, stars=body.stars, settings=get_settings())
    except Exception as exc:
        log.warning("bff.authenticity.engine_error", org_id=ctx.org_id, error=str(exc))
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
        "bff.authenticity.scored",
        org_id=ctx.org_id,
        label=result.label.value,
        flags=[f.value for f in result.flags],
    )
    return result.model_dump(mode="json")


@router.post("/reply", response_model=ReplyDraft)
async def bff_draft_reply(
    body: ReplyRequest,
    ctx: Annotated[ApiKeyContext, Depends(require_session)],
) -> ReplyDraft:
    """Draft a vernacular-native reply for a single review (BFF path)."""
    cache_key = f"{ctx.org_id}:{body.cache_key()}"
    cached = _DRAFT_CACHE.get(cache_key)
    if cached is not None:
        log.info("bff.reply.cache_hit", org_id=ctx.org_id)
        REPLY_CACHE_HIT_TOTAL.inc()
        return cached

    try:
        draft, tokens_in, tokens_out = await draft_reply(body)
    except VernacularModelUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Reply service temporarily unavailable. Please try again shortly.",
            headers={"Retry-After": "60"},
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Reply service temporarily unavailable. Please try again shortly.",
            headers={"Retry-After": "30"},
        ) from exc

    await asyncio.to_thread(update_usage_tokens, ctx.usage_record_id, tokens_in, tokens_out)
    _DRAFT_CACHE[cache_key] = draft
    log.info(
        "bff.reply.drafted",
        org_id=ctx.org_id,
        language=draft.language,
        tone=draft.tone.value,
        model=draft.model_used,
    )
    return draft


@router.post("/corrections", status_code=status.HTTP_201_CREATED)
async def bff_submit_correction(
    body: CorrectionRequest,
    ctx: Annotated[ApiKeyContext, Depends(require_session_read)],
) -> dict[str, Any]:
    """Submit a correction for a review field (BFF path)."""
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
        log.warning("bff.correction.submit_failed", org_id=ctx.org_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store correction.",
        ) from exc
    CORRECTIONS_SUBMITTED.labels(source_type=body.source_type.value).inc()
    log.info(
        "bff.correction.submitted",
        org_id=ctx.org_id,
        source_type=body.source_type.value,
        review_id=body.review_id,
    )
    return {"id": inserted_id, "org_id": ctx.org_id, "review_id": body.review_id}


@router.get("/corrections")
async def bff_list_corrections(
    ctx: Annotated[ApiKeyContext, Depends(require_session_read)],
    source_type: SourceType | None = Query(None),
    review_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List corrections for this org (BFF path)."""
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


@router.get("/insights/trends")
async def bff_theme_trends(
    ctx: Annotated[ApiKeyContext, Depends(require_session_read)],
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    bucket: str = Query("week"),
    trend_of: str = Query("topics"),
    product: str | None = Query(None),
    language: str | None = Query(None),
    limit: int = Query(10, ge=1, le=50),
) -> dict[str, Any]:
    """Theme trends over time (BFF path)."""
    if bucket not in _VALID_BUCKETS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"bucket must be one of {sorted(_VALID_BUCKETS)}, got {bucket!r}.",
        )
    if trend_of not in _VALID_TREND_OF:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"trend_of must be one of {sorted(_VALID_TREND_OF)}, got {trend_of!r}.",
        )

    raw = await asyncio.to_thread(
        theme_trends_pg,
        ctx.org_id,
        since=since,
        until=until,
        bucket=bucket,
        trend_of=trend_of,
        product=product,
        language=language,
        limit=limit,
    )

    themes_out: list[dict[str, Any]] = []
    for t in raw["themes"]:
        sorted_periods = t["sorted_periods"]
        by_period: dict[Any, dict[str, int]] = t["by_period"]
        series: list[dict[str, Any]] = []
        for period_dt in sorted_periods:
            period_str = (
                period_dt.date().isoformat() if hasattr(period_dt, "date") else str(period_dt)
            )
            count = sum(by_period[period_dt].values())
            series.append({"period": period_str, "count": count})
        delta_last, pct_change = _compute_delta(series)
        themes_out.append(
            {
                "theme": t["theme"],
                "total": t["total"],
                "series": series,
                "delta_last": delta_last,
                "pct_change": pct_change,
                "by_language": t["by_language"],
            }
        )

    return {
        "org_id": ctx.org_id,
        "window": {
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "bucket": bucket,
            "trend_of": trend_of,
        },
        "filters": {"product": product, "language": language},
        "themes": themes_out,
    }


@router.get("/insights/health-score")
async def bff_health_score(
    ctx: Annotated[ApiKeyContext, Depends(require_session_read)],
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    days: int = Query(30, ge=1, le=365),
) -> dict[str, Any]:
    """Org-level health score (BFF path)."""
    effective_since = (
        since
        if since is not None
        else datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
    )

    raw = await asyncio.to_thread(health_score_pg, ctx.org_id, effective_since, until)
    total = raw["total_extractions"]

    s_score = _safe_rate(raw["positive_count"], total)
    u_score = 1.0 - _safe_rate(raw["high_urgency_count"], total) if total > 0 else 1.0
    total_audited = raw["total_audited"]
    a_score = (
        1.0 - _safe_rate(raw["likely_fake_count"], total_audited) if total_audited > 0 else 1.0
    )

    score = round(_W_S * s_score + _W_U * u_score + _W_A * a_score, 4)
    authenticity_coverage = _safe_rate(total_audited, total)

    return {
        "org_id": ctx.org_id,
        "window": {
            "since": effective_since.isoformat(),
            "until": until.isoformat() if until else None,
            "days": days,
        },
        "total_extractions": total,
        "components": {
            "sentiment": {
                "score": round(s_score, 4),
                "positive_count": raw["positive_count"],
                "total": total,
                "weight": _W_S,
            },
            "urgency": {
                "score": round(u_score, 4),
                "high_urgency_count": raw["high_urgency_count"],
                "total": total,
                "weight": _W_U,
            },
            "authenticity": {
                "score": round(a_score, 4),
                "priority_review_count": raw["likely_fake_count"],
                "total_audited": total_audited,
                "weight": _W_A,
            },
        },
        "authenticity_coverage": authenticity_coverage,
        "score": score,
        "band": _assign_band(score),
        "confidence": _assign_confidence(total),
        "formula_version": _FORMULA_VERSION,
        "moderation_note": _HS_NOTE,
    }


@router.get("/insights/authenticity")
async def bff_authenticity_summary(
    ctx: Annotated[ApiKeyContext, Depends(require_session_read)],
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    bucket: str = Query("week"),
) -> dict[str, Any]:
    """Aggregated authenticity audit insights (BFF path)."""
    if bucket not in _VALID_BUCKETS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"bucket must be one of {sorted(_VALID_BUCKETS)}, got {bucket!r}.",
        )

    raw = await asyncio.to_thread(
        authenticity_audit_summary_pg,
        ctx.org_id,
        since,
        until,
        bucket,
    )

    total: int = raw["total_audited"]
    n_clear = int(raw["label_genuine"])
    n_flagged = int(raw["label_suspicious"])
    n_priority = int(raw["label_likely_fake"])

    dispositions = {
        "clear": n_clear,
        "flagged_for_review": n_flagged,
        "priority_review": n_priority,
    }
    disposition_rates = {
        "clear": _safe_rate(n_clear, total),
        "flagged_for_review": _safe_rate(n_flagged, total),
        "priority_review": _safe_rate(n_priority, total),
    }
    review_flag_rate = _safe_rate(n_flagged + n_priority, total)

    signal_frequency = [
        {"signal": _map_signal(entry["flag"]), "count": entry["count"]}
        for entry in raw["flag_frequency"]
    ]

    flag_rate_series = []
    for entry in raw["time_series"]:
        period_dt = entry["period"]
        period_str = period_dt.date().isoformat() if hasattr(period_dt, "date") else str(period_dt)
        audited_in_bucket = int(entry["audited"])
        flagged_in_bucket = int(entry["flagged"])
        flag_rate_series.append(
            {
                "period": period_str,
                "review_flag_rate": _safe_rate(flagged_in_bucket, audited_in_bucket),
                "audited": audited_in_bucket,
            }
        )

    return {
        "org_id": ctx.org_id,
        "window": {
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "bucket": bucket,
        },
        "total_audited": total,
        "dispositions": dispositions,
        "disposition_rates": disposition_rates,
        "review_flag_rate": review_flag_rate,
        "mean_authenticity_score": raw["mean_score"],
        "signal_frequency": signal_frequency,
        "flag_rate_series": flag_rate_series,
        "moderation_note": _MODERATION_NOTE,
    }


@router.post("/ingest/csv", status_code=status.HTTP_202_ACCEPTED)
async def bff_ingest_csv(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    ctx: Annotated[ApiKeyContext, Depends(require_session)],
    text_column: Annotated[str | None, Form()] = None,
    product_column: Annotated[str | None, Form()] = None,
    include_authenticity: Annotated[bool, Form()] = False,
) -> dict[str, object]:
    """Upload a CSV of reviews for bulk extraction (BFF path)."""

    try:
        rows, resolved_text, resolved_product = await read_and_validate_csv(
            file, text_column, product_column
        )
    except FileTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc
    except RowLimitExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc
    except CsvColumnError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    if not rows:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="CSV contains no non-empty rows in the text column.",
        )

    job_id = str(uuid.uuid4())
    total = len(rows)
    initial_meta = json.dumps(
        {
            "text_column": resolved_text,
            "product_column": resolved_product,
            "input_hashes": [],
        }
    )

    await asyncio.to_thread(create_batch_job_pg, ctx.org_id, job_id, total, initial_meta)
    background_tasks.add_task(_process_ingest_job, ctx, job_id, rows, include_authenticity)

    log.info("bff.ingest.job_created", job_id=job_id, total=total, org_id=ctx.org_id)
    return {"job_id": job_id, "total": total, "status": "pending"}


@router.get("/ingest/{job_id}")
async def bff_get_ingest_status(
    job_id: str,
    ctx: Annotated[ApiKeyContext, Depends(require_session_read)],
) -> dict[str, object]:
    """Poll the status of a CSV ingest job (BFF path)."""
    job = await asyncio.to_thread(get_batch_job_pg, ctx.org_id, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "total": job["total"],
        "processed": job["processed"],
        "failed": job["failed"],
        "created_at": str(job["created_at"]),
        "completed_at": str(job["completed_at"]) if job.get("completed_at") else None,
    }


@router.get("/dataset")
async def bff_get_dataset(
    ctx: Annotated[ApiKeyContext, Depends(require_session_read)],
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Return the org's structured review dataset, paginated (BFF path)."""
    try:
        records = await asyncio.to_thread(get_dataset_page, ctx.org_id, limit, offset)
    except Exception as exc:
        log.warning("bff.dataset.fetch_failed", org_id=ctx.org_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch dataset.",
        ) from exc
    return {
        "org_id": ctx.org_id,
        "count": len(records),
        "offset": offset,
        "limit": limit,
        "records": records,
    }


@router.get("/account")
async def bff_account(
    ctx: Annotated[ApiKeyContext, Depends(require_session_read)],
) -> dict[str, Any]:
    """Return org-level account summary (BFF path).

    Deliberately omits stored hash, prefix, and raw key material —
    the browser should never see API key internals.
    """
    quota, usage_this_month = await asyncio.to_thread(
        _get_quota_and_usage, ctx.api_key_id, ctx.org_id
    )
    return {
        "org_id": ctx.org_id,
        "quota": quota,
        "usage_this_month": usage_this_month,
    }


class QuotaRequestBody(BaseModel):
    notes: str | None = None


@router.post("/quota-requests", status_code=status.HTTP_201_CREATED)
async def bff_request_quota_increase(
    body: QuotaRequestBody,
    ctx: Annotated[ApiKeyContext, Depends(require_session_read)],
) -> dict[str, Any]:
    """Record interest in a higher monthly quota.

    Stores org_id + current usage so we can see demand and reach out
    when tiered billing is ready. No payment or commitment implied.
    """
    quota, usage_this_month = await asyncio.to_thread(
        _get_quota_and_usage, ctx.api_key_id, ctx.org_id
    )
    await asyncio.to_thread(
        record_quota_request_pg,
        ctx.org_id,
        usage_this_month,
        quota,
        body.notes,
    )
    log.info("bff.quota_request.recorded", org_id=ctx.org_id, usage=usage_this_month, quota=quota)
    return {"recorded": True, "org_id": ctx.org_id}
