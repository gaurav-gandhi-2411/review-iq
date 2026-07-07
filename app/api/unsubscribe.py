"""GET/POST /unsubscribe — public, token-protected, no login required.

Linked from every alert email's body and List-Unsubscribe header (see
app/core/alerts/unsubscribe.py for token generation). GET serves a human
clicking the link in the email body; POST is what mail clients (e.g. Gmail)
call automatically per RFC 8058 one-click unsubscribe. Both perform the same
action: clear organizations.notification_email, which is the single choke
point engine.py and digest.py already check before sending anything.
"""

from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import HTMLResponse

from app.core.alerts.storage import set_org_notification_email_pg
from app.core.alerts.unsubscribe import verify_unsubscribe_token
from app.core.config import get_settings

router = APIRouter(tags=["unsubscribe"])
log = structlog.get_logger(__name__)

_OK_HTML = """<!doctype html><html><head><title>Unsubscribed</title></head>
<body style="font-family:sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem">
<h1>You're unsubscribed</h1>
<p>Samidha Reviews alert emails have been turned off for this account.
You can re-enable them anytime from your Samidha Reviews notification settings.</p>
</body></html>"""

_INVALID_HTML = """<!doctype html><html><head><title>Invalid link</title></head>
<body style="font-family:sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem">
<h1>This unsubscribe link is invalid or expired</h1>
<p>Log in to Samidha Reviews and update your notification settings instead.</p>
</body></html>"""


async def _do_unsubscribe(org: str, token: str) -> HTMLResponse:
    settings = get_settings()
    if not settings.unsubscribe_signing_key:
        log.error("unsubscribe.not_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unsubscribe endpoint not configured on this server.",
        )
    if not verify_unsubscribe_token(org, token):
        log.warning("unsubscribe.token_rejected", org_id=org)
        return HTMLResponse(content=_INVALID_HTML, status_code=status.HTTP_400_BAD_REQUEST)

    await asyncio.to_thread(set_org_notification_email_pg, org, None)
    log.info("unsubscribe.done", org_id=org)
    return HTMLResponse(content=_OK_HTML, headers={"Cache-Control": "no-store"})


@router.get("/unsubscribe")
async def unsubscribe_get(org: str = Query(...), token: str = Query(...)) -> HTMLResponse:
    """Human clicking the unsubscribe link in an email body."""
    return await _do_unsubscribe(org, token)


@router.post("/unsubscribe")
async def unsubscribe_post(org: str = Query(...), token: str = Query(...)) -> HTMLResponse:
    """RFC 8058 one-click target — mail clients POST here automatically."""
    return await _do_unsubscribe(org, token)
