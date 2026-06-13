"""GET /v2/insights/authenticity — tenant-scoped authenticity audit summary."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Query

from app.auth.api_key import ApiKeyContext, require_api_key
from app.core.storage_pg import authenticity_audit_summary_pg

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
        from fastapi import HTTPException, status

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
