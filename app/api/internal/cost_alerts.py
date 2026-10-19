"""POST /internal/cost-alerts/run -- token-protected trigger for the per-customer inference cost sweep (S29, flag OFF).

Called by a scheduler (Cloud Scheduler HTTP target, daily) with the shared-secret token in a header, same shape as
/internal/digest/run. With ENABLE_COST_ALERTS off (the default) it checks the token and does nothing else.
"""

from __future__ import annotations

import asyncio
import hmac

import structlog
from fastapi import APIRouter, Header, HTTPException, status

from app.core.config import get_settings
from app.core.cost_alerts import parse_org_plans, run_sweep
from app.core.storage_pg import org_month_cost_usd_pg

router = APIRouter(prefix="/internal", tags=["internal"])
log = structlog.get_logger(__name__)


def _verify_trigger_token(provided: str | None) -> None:
    """503 when no token is configured (misconfiguration), 401 when it is missing or wrong; timing-safe compare."""
    configured = get_settings().cost_alert_trigger_token
    if not configured:
        log.error("cost_alert_trigger.not_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cost-alert trigger endpoint not configured on this server.",
        )
    if not provided or not hmac.compare_digest(provided, configured):
        log.warning("cost_alert_trigger.token_rejected")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing trigger token."
        )


@router.post("/cost-alerts/run")
async def run_cost_alert_sweep(
    x_cost_alert_trigger_token: str | None = Header(default=None),
) -> dict[str, object]:
    _verify_trigger_token(x_cost_alert_trigger_token)
    settings = get_settings()
    if not settings.enable_cost_alerts:
        return {"ok": True, "enabled": False}
    try:
        org_plans = parse_org_plans(settings.org_plans_json)
    except ValueError as e:  # includes json.JSONDecodeError: a broken alert config must be loud, not silently empty
        log.error("cost_alert_trigger.bad_org_plans_json", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="ORG_PLANS_JSON is invalid."
        ) from e
    result = await asyncio.to_thread(
        run_sweep, org_plans, org_month_cost_usd_pg, None, settings.cost_alert_fraction
    )
    return {"ok": True, "enabled": True, **result}
