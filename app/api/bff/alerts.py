"""BFF alert-preferences endpoints — GET/PUT /bff/alerts/preferences.

Quota: GET uses require_session_read (quota-exempt, read-only).
       PUT uses require_session (write — counts against quota like other writes).
       Alert preference changes are lightweight admin operations, but using
       require_session keeps the auth model consistent with other BFF writes.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator

from app.auth.api_key import ApiKeyContext
from app.auth.session import require_session, require_session_read
from app.core.alerts.rules import AlertEventType
from app.core.alerts.storage import (
    get_all_preferences_pg,
    get_org_notification_email_pg,
    set_org_notification_email_pg,
    upsert_preference_pg,
)

router = APIRouter()
log = structlog.get_logger(__name__)

_VALID_EVENT_TYPES: frozenset[str] = frozenset(e.value for e in AlertEventType)
_VALID_FREQUENCIES: frozenset[str] = frozenset({"immediate", "daily_digest"})


class AlertPrefUpdate(BaseModel):
    enabled: bool
    frequency: str = "immediate"

    @field_validator("frequency")
    @classmethod
    def validate_frequency(cls, v: str) -> str:
        if v not in _VALID_FREQUENCIES:
            raise ValueError(f"frequency must be one of {sorted(_VALID_FREQUENCIES)}")
        return v


class NotificationEmailUpdate(BaseModel):
    email: str | None = None


@router.get("/alerts/preferences")
async def bff_get_alert_preferences(
    ctx: Annotated[ApiKeyContext, Depends(require_session_read)],
) -> dict[str, Any]:
    """Return alert preferences and notification email for the authenticated org.

    Missing event types are returned with conservative defaults (enabled, immediate).
    """
    prefs, notification_email = await asyncio.gather(
        asyncio.to_thread(get_all_preferences_pg, ctx.org_id),
        asyncio.to_thread(get_org_notification_email_pg, ctx.org_id),
    )
    return {
        "org_id": ctx.org_id,
        "notification_email": notification_email,
        "preferences": prefs,
    }


@router.put("/alerts/preferences/{event_type}", status_code=status.HTTP_200_OK)
async def bff_put_alert_preference(
    event_type: str,
    body: AlertPrefUpdate,
    ctx: Annotated[ApiKeyContext, Depends(require_session)],
) -> dict[str, Any]:
    """Set enabled/frequency for one alert event type."""
    if event_type not in _VALID_EVENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown event_type '{event_type}'. Valid: {sorted(_VALID_EVENT_TYPES)}",
        )
    await asyncio.to_thread(
        upsert_preference_pg, ctx.org_id, event_type, body.enabled, body.frequency
    )
    log.info(
        "bff.alerts.pref_updated",
        org_id=ctx.org_id,
        event_type=event_type,
        enabled=body.enabled,
        frequency=body.frequency,
    )
    return {
        "ok": True,
        "org_id": ctx.org_id,
        "event_type": event_type,
        "enabled": body.enabled,
        "frequency": body.frequency,
    }


@router.put("/alerts/notification-email", status_code=status.HTTP_200_OK)
async def bff_put_notification_email(
    body: NotificationEmailUpdate,
    ctx: Annotated[ApiKeyContext, Depends(require_session)],
) -> dict[str, Any]:
    """Set or clear the notification email for alert delivery."""
    await asyncio.to_thread(set_org_notification_email_pg, ctx.org_id, body.email)
    log.info("bff.alerts.email_updated", org_id=ctx.org_id, has_email=body.email is not None)
    return {"ok": True, "org_id": ctx.org_id, "notification_email": body.email}
