"""GET /v2/insights/authenticity, /trends, and /health-score — tenant-scoped insight endpoints."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.api_key import ApiKeyContext, require_api_key
from app.core.storage_pg import authenticity_audit_summary_pg, health_score_pg, theme_trends_pg

router = APIRouter(prefix="/v2/insights", tags=["v2-insights"])
log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Precision-first display mappings
#
# Stored labels (genuine / suspicious / likely_fake) MUST NOT appear in API
# responses — they carry implicit guilt.  These constants map internal values
# to administrator-signal language (IS 19000:2022 compliance posture).
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

_MODERATION_NOTE = (
    "Signals support human moderation under IS 19000:2022; "
    "dispositions are review priorities for an administrator, not verdicts."
)


def _map_disposition(raw: str) -> str:
    """Map a stored label to its display-safe disposition string.

    Falls back to ``"review"`` for any value not in DISPOSITION_DISPLAY so
    unknown future labels never surface raw stored text in the API response.
    """
    return DISPOSITION_DISPLAY.get(raw, "review")


def _map_signal(raw: str) -> str:
    """Map a stored flag value to its display-safe signal string.

    Falls back to ``"other_signal"`` for unknown values.
    """
    return SIGNAL_DISPLAY.get(raw, "other_signal")


def _safe_rate(numerator: int, denominator: int) -> float:
    """Return numerator / denominator, or 0.0 when denominator is zero."""
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 6)


@router.get("/authenticity")
async def authenticity_summary(
    ctx: ApiKeyContext = Depends(require_api_key),
    since: datetime | None = Query(None, description="ISO8601 lower bound on audit created_at"),
    until: datetime | None = Query(None, description="ISO8601 upper bound on audit created_at"),
    bucket: str = Query("week", description="Time-series granularity: day | week | month"),
) -> dict[str, Any]:
    """Aggregated authenticity audit insights for the authenticated org.

    Dispositions and signal names use administrator-signal wording (IS 19000:2022)
    — no raw stored labels are echoed in the response.

    Returns 422 when ``bucket`` is not one of ``day``, ``week``, or ``month``.
    """
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

    # --- disposition counts (mapped) ---
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

    # --- signal frequency (mapped) ---
    signal_frequency = [
        {"signal": _map_signal(entry["flag"]), "count": entry["count"]}
        for entry in raw["flag_frequency"]
    ]

    # --- time series (mapped) ---
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

    log.info(
        "insights.authenticity",
        org_id=ctx.org_id,
        total_audited=total,
        review_flag_rate=review_flag_rate,
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


# ---------------------------------------------------------------------------
# Trends helpers
# ---------------------------------------------------------------------------

# Whitelist of allowed values for the ``trend_of`` parameter.  Validation here
# mirrors the whitelist in storage_pg._TREND_OF_COLUMNS: user input never
# reaches SQL as an identifier — only the dict's value does.
_VALID_TREND_OF = frozenset({"topics", "cons"})


def _compute_delta(series: list[dict[str, Any]]) -> tuple[int, float | None]:
    """Compute latest-minus-prior bucket delta and percent change.

    Args:
        series: Chronologically-ordered list of ``{"period": str, "count": int}``.

    Returns:
        ``(delta_last, pct_change)`` where ``pct_change`` is ``None`` when the
        prior bucket count is 0 (guard against division by zero).
        Both values are 0 / ``None`` when the series has fewer than 2 entries.
    """
    if len(series) < 2:
        return 0, None
    latest = series[-1]["count"]
    prior = series[-2]["count"]
    delta = latest - prior
    pct: float | None = None if prior == 0 else round((latest - prior) / prior, 6)
    return delta, pct


@router.get("/trends")
async def theme_trends(
    ctx: ApiKeyContext = Depends(require_api_key),
    since: datetime | None = Query(None, description="ISO8601 lower bound on created_at"),
    until: datetime | None = Query(None, description="ISO8601 upper bound on created_at"),
    bucket: str = Query("week", description="Time-series granularity: day | week | month"),
    trend_of: str = Query("topics", description="JSONB column to trend: topics | cons"),
    product: str | None = Query(None, description="Filter by product name (partial match)"),
    language: str | None = Query(None, description="Filter by exact language code"),
    limit: int = Query(10, ge=1, le=50, description="Maximum number of themes to return"),
) -> dict[str, Any]:
    """Complaint / theme trends over time with per-language breakdown.

    Returns the top-N themes (ordered by total count descending) with:
    - A chronological count series (summed across languages).
    - Per-language counts (the India-vernacular differentiator: en / hi-en / hi).
    - Delta and percent-change between the latest and prior bucket.

    Returns 422 when ``bucket`` is not ``day``, ``week``, or ``month``,
    or when ``trend_of`` is not ``topics`` or ``cons``.
    """
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
        # Build chronological series — sum across all languages per period.
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

    log.info(
        "insights.trends",
        org_id=ctx.org_id,
        trend_of=trend_of,
        bucket=bucket,
        themes_returned=len(themes_out),
    )

    return {
        "org_id": ctx.org_id,
        "window": {
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "bucket": bucket,
            "trend_of": trend_of,
        },
        "filters": {
            "product": product,
            "language": language,
        },
        "themes": themes_out,
    }


# ---------------------------------------------------------------------------
# Health-score endpoint
# ---------------------------------------------------------------------------

_FORMULA_VERSION = "1.0"

# Component weights — must sum to 1.0.
# 0.50 S: sentiment drives score variation most (fake-rate is near-constant and low).
# 0.20 U: urgency provides a secondary signal without dominating.
# 0.30 A: authenticity can crater the score on a fake spike; 0.30 preserves that signal.
_W_S: float = 0.50
_W_U: float = 0.20
_W_A: float = 0.30

# Band thresholds (score in [0, 1]).  Proposed based on real DB distribution:
#   gaurav-dev=0.445 → at_risk, 1-review orgs=0.50 → needs_attention,
#   Eval=0.632 → needs_attention, Quota Test=1.00 → healthy.
# 75/50 is mathematically grounded: 0.50 is the formula midpoint and should
# not default to "at_risk" for orgs with sparse data.
_BAND_HEALTHY: float = 0.75
_BAND_NEEDS_ATTENTION: float = 0.50

# Confidence thresholds: number of extractions in the window.
_CONFIDENCE_HIGH: int = 50
_CONFIDENCE_MEDIUM: int = 10

_HS_NOTE = (
    "Health score is an org-level aggregate and does not label or score any individual review. "
    "Authenticity signals support human moderation under IS 19000:2022."
)


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


@router.get("/health-score")
async def health_score(
    ctx: ApiKeyContext = Depends(require_api_key),
    since: datetime | None = Query(None, description="ISO8601 lower bound on created_at"),
    until: datetime | None = Query(None, description="ISO8601 upper bound on created_at"),
    days: int = Query(30, ge=1, le=365, description="Rolling window in days (ignored when since is set)"),
) -> dict[str, Any]:
    """Org-level health score for the authenticated org.

    Combines three components into a single [0, 1] score:
    - **Sentiment (S, weight 0.50):** fraction of positive reviews.
    - **Urgency (U, weight 0.20):** 1 − fraction of high-urgency reviews.
    - **Authenticity (A, weight 0.30):** 1 − likely-flagged rate; 1.0 when unaudited.

    ``score = 0.50·S + 0.20·U + 0.30·A``

    ``confidence`` reflects data volume and is held separate from ``score``
    so callers can weight the band assignment accordingly.

    Returns 200 for all authenticated orgs, including those with no data
    (score = 0.50, confidence = "low", band = "needs_attention").
    """
    # When `since` is not provided, default to a rolling window of `days` days.
    effective_since = since if since is not None else datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)

    raw = await asyncio.to_thread(health_score_pg, ctx.org_id, effective_since, until)

    total = raw["total_extractions"]

    # --- component scores ---
    s_score = _safe_rate(raw["positive_count"], total)
    u_score = 1.0 - _safe_rate(raw["high_urgency_count"], total) if total > 0 else 1.0
    total_audited = raw["total_audited"]
    # Spec: A = 1.0 when total_audited = 0; only likely_fake penalises.
    a_score = 1.0 - _safe_rate(raw["likely_fake_count"], total_audited) if total_audited > 0 else 1.0

    score = round(_W_S * s_score + _W_U * u_score + _W_A * a_score, 4)
    authenticity_coverage = _safe_rate(total_audited, total)

    log.info(
        "insights.health_score",
        org_id=ctx.org_id,
        total_extractions=total,
        total_audited=total_audited,
        score=score,
        band=_assign_band(score),
    )

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
                # Map stored label to precision-first display name.
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
