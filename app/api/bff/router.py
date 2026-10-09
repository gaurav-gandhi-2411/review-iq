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
import csv
import io
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import psycopg2
import structlog
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field, field_validator, model_validator

# _drain_until_job_complete is defined in app.api.v2.ingest alongside the
# durable batch_job_rows queue it drains (app/core/ingest_worker.py). It is a
# pure business-logic coroutine with no coupling to the HTTP handler —
# importing it here lets this BFF endpoint share the SAME durable queue as
# POST /v2/ingest/csv (Option B, 2026-07-09) instead of its own fire-and-forget
# path, so a Cloud Run restart mid-job no longer silently drops BFF uploads.
from app.api.v2.ingest import _drain_until_job_complete
from app.api.v2.insights import (
    _FORMULA_VERSION,
    _HS_NOTE,
    _W_S,
    _W_U,
    _assign_confidence,
    compute_health_score,
    health_band,
)
from app.api.v2.reply import ensure_reply_drafting_enabled
from app.auth.api_key import ApiKeyContext
from app.auth.keygen import insert_api_key_with_retry
from app.auth.session import require_session, require_session_read
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
from app.core.detectors.batch_defect import WINDOW_DAYS as BATCH_DEFECT_WINDOW_DAYS
from app.core.detectors.batch_defect import annotated_reviews_from_rows, scan_batch_defects
from app.core.metrics import CORRECTIONS_SUBMITTED, REPLY_CACHE_HIT_TOTAL
from app.core.reply.engine import VernacularModelUnavailableError, draft_reply
from app.core.reply.schema import ReplyDraft, ReplyRequest
from app.core.schemas import Sentiment, Urgency
from app.core.storage_pg import (
    _set_tenant,
    create_batch_job_pg,
    enqueue_batch_job_rows_pg,
    get_batch_job_pg,
    health_score_pg,
    list_dated_extractions_pg,
    list_extractions_pg,
    record_quota_request_pg,
    theme_trends_pg,
    update_batch_job_pg,
    update_usage_tokens,
)

_BATCH_DEFECT_NOTE = (
    "Moderation-prioritization signal only, not a verdict. Synthetic-validated; not yet "
    "validated against real seller data. product_id reflects the extraction's free-text "
    "product field, not a stable product ID."
)

router = APIRouter(prefix="/bff", tags=["bff"])
log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Shared constants copied from v2 endpoints (kept here for colocation)
# ---------------------------------------------------------------------------

_VALID_BUCKETS = frozenset({"day", "week", "month"})
_VALID_TREND_OF = frozenset({"topics", "cons"})

# Per-process reply cache — same ephemeral approach as v2/reply.py.
_DRAFT_CACHE: dict[str, ReplyDraft] = {}


# ---------------------------------------------------------------------------
# Request models (copied from v2 endpoints — not imported to avoid
# coupling to HTTP handler boilerplate)
# ---------------------------------------------------------------------------


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


# Bug fix (found in code review after PR #45 landed ungated -- a merge-gate bypass incident,
# see PLAN.md): CreateApiKeyRequest.quota had NO bound at all and was written verbatim to the
# api_keys.quota column that app/auth/api_key.py:99 uses as the sole gate on request admission
# (`if monthly_count >= quota: raise 429`). POST /bff/keys is reachable by any signed-in
# session user for their own org (require_session, no plan/admin check) -- any free-tier user
# could self-issue a key with quota=999999999 and permanently bypass the advertised free-tier
# limit. Two layers now: Field(ge=1) rejects zero/negative outright (quota=0 previously locked
# a key out silently with no validation error -- a second, independent bug this also fixes);
# the real per-plan ceiling is enforced in bff_create_key() below, since it depends on the
# caller's org (Pydantic field constraints can't see request context beyond the field itself).
#
# PLAN_QUOTA_LIMITS' numbers are NOT a sourced pricing decision. "free": 100 is the one figure
# GG has actually committed to (docs/specs/wave1-commercialization.md S0#1: "Free tier is
# asserted (100 extractions/mo)"). "pro"/"enterprise" reuse this field's pre-existing (buggy)
# default of 1000 as a placeholder ceiling -- the real Stripe/billing work that was meant to
# define differentiated tiers (PR #43, "minimum-viable Stripe billing") was merged into a
# stacked branch (fix/wave1-s0-bypassrls-remediation) that never actually reached main despite
# GitHub showing it "merged" -- see PLAN.md's stacked-PR-merge-discipline entry. Revisit these
# two numbers once real billing tiers are decided and actually land on main.
PLAN_QUOTA_LIMITS: dict[str, int] = {
    "free": 100,
    "pro": 1000,
    "enterprise": 1000,
}
# Fail closed to the strictest tier for any plan value not in the table above (a future plan
# added to the DB CHECK constraint but not yet wired here should never default to unlimited).
_DEFAULT_PLAN_QUOTA_LIMIT = PLAN_QUOTA_LIMITS["free"]


class CreateApiKeyRequest(BaseModel):
    name: str
    quota: int = Field(default=1000, ge=1)


# ---------------------------------------------------------------------------
# Export (GET /bff/export/reviews)
# ---------------------------------------------------------------------------

_EXPORT_ROW_CAP = 5000

# Column order for the export -- mirrors GET /bff/reviews' underlying
# list_extractions_pg row shape (see app/core/storage_pg.py::list_extractions_pg),
# minus internal id/input_hash which are not part of the public review shape
# exposed by that endpoint's `results` payload.
_EXPORT_COLUMNS = [
    "review_text",
    "product",
    "stars",
    "stars_inferred",
    "buy_again",
    "sentiment",
    "urgency",
    "language",
    "review_length_chars",
    "confidence",
    "topics",
    "competitor_mentions",
    "pros",
    "cons",
    "feature_requests",
    "created_at",
    "review_date",
]


def _export_value(value: Any) -> Any:
    """Flatten list/None values to export-friendly scalars (CSV has no nested types)."""
    if isinstance(value, list):
        return "; ".join(str(v) for v in value)
    if value is None:
        return ""
    return value


# ---------------------------------------------------------------------------
# Helpers (ported from insights.py)
# ---------------------------------------------------------------------------


def _compute_delta(series: list[dict[str, Any]]) -> tuple[int, float | None]:
    if len(series) < 2:
        return 0, None
    latest = series[-1]["count"]
    prior = series[-2]["count"]
    delta = latest - prior
    pct: float | None = None if prior == 0 else round((latest - prior) / prior, 6)
    return delta, pct


# ---------------------------------------------------------------------------
# Account helpers (DB calls scoped to the caller's own api_key_id / org_id)
# ---------------------------------------------------------------------------


def _get_quota_and_usage(api_key_id: str, org_id: str) -> tuple[int, int]:
    """Return (quota, monthly_usage_count) for the authenticated key.

    _set_tenant()'d for the same defense-in-depth reason as _get_org_plan_pg below
    (BYPASSRLS remediation, 2c): the WHERE clauses already scope both queries to
    this org (api_keys.org_id, usage_records via its api_key_id FK), so no
    cross-tenant read was ever reachable here -- but without _set_tenant() the
    connection runs outside the `authenticated` role RLS actually checks, leaving
    no second layer if a future edit to the WHERE clause introduces a bug.
    """
    import psycopg2 as _psycopg2

    from app.core.config import get_settings as _gs

    conn = _psycopg2.connect(_gs().supabase_database_url)
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            "SELECT quota FROM public.api_keys WHERE id = %s AND org_id = %s",
            (api_key_id, org_id),
        )
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
# API key management helpers (self-serve, session-authed)
#
# SQL logic mirrors app/api/admin.py's _create_key_db / _list_keys_db /
# _revoke_key_db exactly (same api_keys table, same generate_api_key() usage,
# same org-scoped WHERE clauses) -- only the auth mechanism differs: org_id
# here is ALWAYS ctx.org_id resolved from the verified session, never a path
# parameter, so a caller can never operate on another org's keys. Unlike
# admin.py (single org, path-addressed) this is multi-tenant self-serve, so
# every query is scoped by org_id in addition to key_id.
# ---------------------------------------------------------------------------


def _keys_db_connect() -> psycopg2.extensions.connection:
    return psycopg2.connect(get_settings().supabase_database_url)


def _get_org_plan_pg(org_id: str) -> str:
    """Return this org's plan tier (organizations.plan -- CHECK constraint limits it to
    'free'/'pro'/'enterprise'). Used only to bound self-serve API key quota requests against
    the caller's actual entitlement -- see PLAN_QUOTA_LIMITS above bff_create_key().

    Bug fix (same pass as the RLS fix below): this and the three functions below all connect
    via bare psycopg2.connect() and never called _set_tenant() -- every other function in
    app/core/storage_pg.py applies it as a defense-in-depth layer on top of the WHERE-clause
    scoping (both organizations and api_keys have RLS policies requiring
    app.current_org_id() -- see supabase/migrations/20260510000002_rls_policies.sql). The
    WHERE org_id = %s predicates below were already correct (no cross-tenant read/write was
    ever possible), but without _set_tenant() the connection runs outside the `authenticated`
    role RLS actually checks, so a bug in a future edit to the WHERE clause would have no
    second layer catching it."""
    conn = _keys_db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute("SELECT plan FROM public.organizations WHERE id = %s", (org_id,))
        row = cur.fetchone()
        conn.commit()
        return str(row[0]) if row else "free"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _create_key_bff_db(org_id: str, name: str, quota: int) -> dict[str, object]:
    """Insert a new api_keys row scoped to org_id. Returns the raw key exactly once.

    Retries on a key_prefix collision (see app/auth/keygen.py's
    insert_api_key_with_retry docstring) -- api_keys.key_prefix carries a real UNIQUE
    constraint as of the BYPASSRLS remediation migration.
    """
    conn = _keys_db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        row: tuple[object, object] | None = None

        def _do_insert(raw_key: str, key_prefix: str, key_hash: str) -> None:
            nonlocal row
            cur.execute(
                "INSERT INTO public.api_keys (org_id, key_prefix, key_hash, name, quota) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id, created_at",
                (org_id, key_prefix, key_hash, name, quota),
            )
            row = cur.fetchone()

        raw_key, key_prefix, _key_hash = insert_api_key_with_retry(cur, _do_insert)
        assert row is not None
        conn.commit()
        return {
            "id": str(row[0]),
            "raw_key": raw_key,
            "key_prefix": key_prefix,
            "name": name,
            "quota": quota,
            "created_at": row[1],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _list_keys_bff_db(org_id: str) -> list[dict[str, object]]:
    """List non-revoked keys for org_id. Never returns key_hash or the raw key."""
    conn = _keys_db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            "SELECT id, name, key_prefix, quota, created_at FROM public.api_keys "
            "WHERE org_id = %s AND revoked_at IS NULL ORDER BY created_at DESC",
            (org_id,),
        )
        rows = cur.fetchall()
        conn.commit()
        return [
            {
                "id": str(r[0]),
                "name": r[1],
                "key_prefix": r[2],
                "quota": r[3],
                "created_at": r[4],
            }
            for r in rows
        ]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _revoke_key_bff_db(org_id: str, key_id: str) -> None:
    """Revoke a key -- WHERE clause binds BOTH id and org_id, so a caller can
    never revoke another org's key by guessing a UUID (see test_bff_keys.py::
    test_revoke_key_cross_org_isolation)."""
    conn = _keys_db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            "UPDATE public.api_keys SET revoked_at = now() "
            "WHERE id = %s AND org_id = %s AND revoked_at IS NULL RETURNING id",
            (key_id, org_id),
        )
        if cur.fetchone() is None:
            conn.rollback()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Key not found or already revoked.",
            )
        conn.commit()
    except HTTPException:
        raise
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
    topic: str | None = Query(
        None,
        description="Exact (case-sensitive) match against one entry of the topics list",
    ),
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


@router.get("/export/reviews")
async def bff_export_reviews(
    ctx: Annotated[ApiKeyContext, Depends(require_session_read)],
    format: str = Query("csv", pattern="^(csv|json)$"),
) -> Response:
    """Export the caller's own org's review extractions as CSV or JSON.

    Reuses list_extractions_pg -- the same underlying query GET /bff/reviews uses --
    rather than a separate export-specific query. Capped at _EXPORT_ROW_CAP rows;
    truncation is surfaced via the X-Truncated header, never silently dropped.
    """
    rows = await asyncio.to_thread(
        list_extractions_pg,
        ctx.org_id,
        limit=_EXPORT_ROW_CAP,
        offset=0,
    )
    truncated = len(rows) >= _EXPORT_ROW_CAP
    headers = {"X-Truncated": "true"} if truncated else {}

    if format == "json":
        export_rows = [{col: row.get(col) for col in _EXPORT_COLUMNS} for row in rows]
        return Response(
            content=json.dumps(export_rows, default=str),
            media_type="application/json",
            headers=headers,
        )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_EXPORT_COLUMNS)
    for row in rows:
        writer.writerow([_export_value(row.get(col)) for col in _EXPORT_COLUMNS])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={**headers, "Content-Disposition": "attachment; filename=reviews.csv"},
    )


@router.post("/reply", response_model=ReplyDraft)
async def bff_draft_reply(
    body: ReplyRequest,
    ctx: Annotated[ApiKeyContext, Depends(require_session)],
) -> ReplyDraft:
    """Draft a vernacular-native reply for a single review (BFF path).

    Returns 503 {"code": "reply_drafting_disabled"} without any provider call while the
    ENABLE_REPLY_DRAFTING kill switch is off (the default).
    """
    ensure_reply_drafting_enabled()
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

    await asyncio.to_thread(
        update_usage_tokens, ctx.org_id, ctx.usage_record_id, tokens_in, tokens_out
    )
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


@router.get("/insights/batch-defects")
async def bff_batch_defects(
    ctx: Annotated[ApiKeyContext, Depends(require_session_read)],
    min_confidence: float = Query(0.0, ge=0.0, le=1.0),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """Batch-defect (topic spike) detector, off by default (BFF path) -- see
    app/api/v2/insights.py::batch_defects for the full docstring; this mirrors it exactly."""
    if not get_settings().enable_batch_defect_detector:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")

    rows = await asyncio.to_thread(list_dated_extractions_pg, ctx.org_id)
    reviews = annotated_reviews_from_rows(rows)
    all_flags = scan_batch_defects(reviews)
    flags = [f.to_dict() for f in all_flags if f.confidence >= min_confidence][:limit]

    return {
        "org_id": ctx.org_id,
        "window": {"scope": "full_history", "spike_window_days": BATCH_DEFECT_WINDOW_DAYS},
        "filters": {"min_confidence": min_confidence, "limit": limit},
        "flags": flags,
        "note": _BATCH_DEFECT_NOTE,
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

    s_score, u_score, score = compute_health_score(raw)

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
                "weight": round(_W_S, 4),
            },
            "urgency": {
                "score": round(u_score, 4),
                "high_urgency_count": raw["high_urgency_count"],
                "total": total,
                "weight": round(_W_U, 4),
            },
        },
        "score": score,
        "band": health_band(raw),
        "confidence": _assign_confidence(total),
        "formula_version": _FORMULA_VERSION,
        "moderation_note": _HS_NOTE,
    }


def _parse_iso(value: str | None) -> datetime | None:
    """Round-trip a review_date string (already-parsed ISO8601, from read_and_validate_csv) back
    into a datetime for storage_pg -- never re-guesses format, this input is already unambiguous.
    Duplicated from app/api/v2/ingest.py (this file already near-duplicates that endpoint's CSV
    logic; tracked as a dedup candidate, not introduced here)."""
    return datetime.fromisoformat(value) if value else None


@router.post("/ingest/csv", status_code=status.HTTP_202_ACCEPTED)
async def bff_ingest_csv(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    ctx: Annotated[ApiKeyContext, Depends(require_session)],
    text_column: Annotated[str | None, Form()] = None,
    product_column: Annotated[str | None, Form()] = None,
    date_column: Annotated[str | None, Form()] = None,
    date_format: Annotated[str | None, Form()] = None,
) -> dict[str, object]:
    """Upload a CSV of reviews for bulk extraction (BFF path).

    `date_column`: optional column holding each review's ORIGINAL post date (auto-detected from
    common names if omitted). Unparseable/ambiguous dates are never fabricated -- left absent,
    reflected in the returned `date_ambiguous` flag. `date_format`: optional "DMY"/"MDY" hint.
    """

    try:
        (
            rows,
            resolved_text,
            resolved_product,
            resolved_date,
            date_ambiguous,
        ) = await read_and_validate_csv(file, text_column, product_column, date_column, date_format)
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

    # Store column mapping now; input_hashes are appended on completion.
    initial_meta = json.dumps(
        {
            "text_column": resolved_text,
            "product_column": resolved_product,
            "date_column": resolved_date,
            "date_ambiguous": date_ambiguous,
            "input_hashes": [],
        }
    )

    await asyncio.to_thread(create_batch_job_pg, ctx.org_id, job_id, total, initial_meta)
    # Durable path (Option B, 2026-07-09): rows are persisted in batch_job_rows
    # here, before any processing starts — if this instance dies before the
    # BackgroundTask below finishes, POST /internal/ingest/tick resumes the
    # remainder on a schedule.
    await asyncio.to_thread(
        enqueue_batch_job_rows_pg,
        ctx.org_id,
        job_id,
        [row["text"] for row in rows],
        [row.get("product") for row in rows],
        [_parse_iso(row.get("review_date")) for row in rows],
    )
    await asyncio.to_thread(update_batch_job_pg, ctx.org_id, job_id, status="processing")

    background_tasks.add_task(_drain_until_job_complete, ctx.org_id, job_id)

    log.info(
        "bff.ingest.job_created",
        job_id=job_id,
        total=total,
        org_id=ctx.org_id,
        date_column=resolved_date,
        date_ambiguous=date_ambiguous,
    )
    return {
        "job_id": job_id,
        "total": total,
        "status": "pending",
        "date_column": resolved_date,
        "date_ambiguous": date_ambiguous,
    }


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
    # api_key_id is str|None on ApiKeyContext (None only for system-triggered extractions like
    # webhooks) but require_session_read's _lookup_context_for_read always sets a real string --
    # this guard is the explicit narrowing mypy needs, not a real "missing key" case here.
    if ctx.api_key_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No API key found for this session.")
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
    # See bff_account's identical guard above -- require_session_read always sets a real
    # api_key_id; this is the explicit narrowing mypy needs, not a real "missing key" case.
    if ctx.api_key_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No API key found for this session.")
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


@router.get("/keys")
async def bff_list_keys(
    ctx: Annotated[ApiKeyContext, Depends(require_session_read)],
) -> dict[str, Any]:
    """List this org's non-revoked API keys. Never returns key_hash or raw key material."""
    keys = await asyncio.to_thread(_list_keys_bff_db, ctx.org_id)
    return {
        "org_id": ctx.org_id,
        "keys": [
            {
                "id": k["id"],
                "name": k["name"],
                "key_prefix": k["key_prefix"],
                "quota": k["quota"],
                "created_at": k["created_at"].isoformat()
                if hasattr(k["created_at"], "isoformat")
                else str(k["created_at"]),
            }
            for k in keys
        ],
    }


@router.post("/keys", status_code=status.HTTP_201_CREATED)
async def bff_create_key(
    body: CreateApiKeyRequest,
    ctx: Annotated[ApiKeyContext, Depends(require_session)],
) -> dict[str, object]:
    """Create a new API key scoped to this org. raw_key is shown exactly once.

    quota is bounded to the org's plan entitlement (PLAN_QUOTA_LIMITS), not to an arbitrary
    global max -- a free-tier org must not be able to set a value above its own tier's limit
    regardless of what it submits (see PLAN_QUOTA_LIMITS' docstring above CreateApiKeyRequest
    for the incident this closes and the provisional nature of the pro/enterprise numbers).
    """
    plan = await asyncio.to_thread(_get_org_plan_pg, ctx.org_id)
    plan_limit = PLAN_QUOTA_LIMITS.get(plan, _DEFAULT_PLAN_QUOTA_LIMIT)
    if body.quota > plan_limit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Requested quota ({body.quota}) exceeds the {plan} plan's limit "
                f"({plan_limit}). Use POST /bff/quota-requests to record interest in a "
                "higher quota."
            ),
        )
    result = await asyncio.to_thread(_create_key_bff_db, ctx.org_id, body.name, body.quota)
    log.info("bff.keys.created", org_id=ctx.org_id, key_id=result["id"])
    created_at = result["created_at"]
    return {
        "id": result["id"],
        "name": result["name"],
        "key_prefix": result["key_prefix"],
        "raw_key": result["raw_key"],
        "quota": result["quota"],
        "created_at": created_at.isoformat()
        if hasattr(created_at, "isoformat")
        else str(created_at),
        "note": "Store this key securely — it will not be shown again.",
    }


@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def bff_revoke_key(
    key_id: uuid.UUID,
    ctx: Annotated[ApiKeyContext, Depends(require_session)],
) -> None:
    """Revoke an API key. WHERE clause is scoped to id AND org_id -- a caller can
    never revoke another org's key by guessing a UUID."""
    await asyncio.to_thread(_revoke_key_bff_db, ctx.org_id, str(key_id))
    log.info("bff.keys.revoked", org_id=ctx.org_id, key_id=str(key_id))


from app.api.bff.alerts import router as _alerts_router  # noqa: E402, I001 -- deliberately after all route handlers, not a top-level import (see module docstring's import constraints)

router.include_router(_alerts_router)
