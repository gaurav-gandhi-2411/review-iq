"""POST /webhooks/resend — bounce / complaint feedback from Resend (S20 M3b item 4).

Resend signs webhooks with Svix. Verification (docs: resend.com/docs/webhooks/verify-webhooks):
  headers   svix-id, svix-timestamp (unix seconds), svix-signature ("v1,<b64> v1,<b64> ...")
  signed    f"{svix-id}.{svix-timestamp}.{raw body}"
  key       base64-decode(RESEND_WEBHOOK_SECRET minus the "whsec_" prefix)
  algorithm HMAC-SHA256, base64-encoded, compared in constant time.

Order of operations (each step fails closed and processes nothing further):
  1. secret unset or malformed        -> 503
  2. missing svix headers / stale or future timestamp (> 5 min) / bad signature -> 401
  3. body > 64 KiB                    -> 413  (checked while streaming, before buffering all of it)
  4. wrong content-type               -> 415
  5. malformed JSON                   -> 400
Only after the signature is verified is the payload parsed.

Handled events:
  email.bounced with bounce.type == "Permanent" (hard bounce) and email.complained:
    * clear organizations.notification_email for every org using that address, through
      set_org_notification_email_pg -- the same single choke point /unsubscribe uses;
    * record the suppression (hash of the address only) so alert and digest senders skip it.
  email.bounced with any other bounce type, email.delivery_delayed: logged only.
  Any other event type: ignored, 200.

Idempotency: the suppression event_id is "<svix-id>#<recipient index>"; Svix reuses svix-id on
retries, and the UNIQUE constraint turns a replay into a no-op. Clearing the notification email
is idempotent by nature.

Email addresses are never logged: only the first 12 hex chars of their sha256.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import time
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request, status

from app.core.alerts.storage import (
    record_email_suppression_pg,
    resolve_orgs_for_notification_email_pg,
    set_org_notification_email_pg,
)
from app.core.config import get_settings

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/webhooks/resend", tags=["webhooks"])

_SECRET_PREFIX = "whsec_"  # noqa: S105 - Svix's documented key prefix, not a credential
_TIMESTAMP_TOLERANCE_SECONDS = 300  # reject deliveries older (or newer) than 5 minutes
_MAX_BODY_BYTES = 64 * 1024  # real bounce events are ~1 KiB; cap unauthenticated input
_MAX_RECIPIENTS = 50
_HTTP_413 = 413  # starlette renamed its constant across versions; the number is stable
_MAX_EMAIL_LEN = 254


def _decode_secret(secret: str) -> bytes | None:
    """Return the raw HMAC key from a 'whsec_<base64>' secret, or None if unusable."""
    raw = secret[len(_SECRET_PREFIX) :] if secret.startswith(_SECRET_PREFIX) else secret
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return None
    return key or None


def verify_svix_signature(
    *,
    secret: str,
    svix_id: str,
    svix_timestamp: str,
    svix_signature: str,
    body: bytes,
    now: float | None = None,
) -> bool:
    """True iff the signature header holds a valid v1 signature and the timestamp is fresh."""
    key = _decode_secret(secret)
    if key is None or not svix_id or not svix_timestamp or not svix_signature:
        return False
    try:
        ts = int(svix_timestamp)
    except ValueError:
        return False
    current = time.time() if now is None else now
    if abs(current - ts) > _TIMESTAMP_TOLERANCE_SECONDS:
        return False
    signed = b".".join([svix_id.encode(), svix_timestamp.encode(), body])
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    # The header may carry several space-separated "v1,<sig>" entries (key rotation).
    ok = False
    for part in svix_signature.split():
        version, _, sig = part.partition(",")
        if version == "v1" and hmac.compare_digest(expected, sig):
            ok = True  # keep looping: do not short-circuit on the first match
    return ok


def _addr_tag(email: str) -> str:
    """Non-reversible log tag for an address; full addresses are never logged."""
    return hashlib.sha256(email.encode()).hexdigest()[:12]


def _normalise_recipients(raw: Any) -> list[str]:
    """Lower-cased, plausible addresses from the event's 'to' field (list or string)."""
    items = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
    out: list[str] = []
    for item in items[:_MAX_RECIPIENTS]:
        if not isinstance(item, str):
            continue
        addr = item.strip().lower()
        # "Name <a@b.c>" style recipients are reduced to the bare address.
        if "<" in addr and addr.endswith(">"):
            addr = addr[addr.rindex("<") + 1 : -1].strip()
        if 3 <= len(addr) <= _MAX_EMAIL_LEN and addr.count("@") == 1 and " " not in addr:
            out.append(addr)
    return out


async def _read_capped_body(request: Request) -> bytes:
    """Read the request body, raising 413 as soon as it exceeds the cap."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > _MAX_BODY_BYTES:
        raise HTTPException(_HTTP_413, "Payload too large.")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _MAX_BODY_BYTES:
            raise HTTPException(_HTTP_413, "Payload too large.")
        chunks.append(chunk)
    return b"".join(chunks)


async def _suppress(svix_id: str, index: int, email: str, reason: str) -> int:
    """Clear the address from every org that uses it, then record the suppression."""
    orgs = await asyncio.to_thread(resolve_orgs_for_notification_email_pg, email)
    for org_id in orgs:
        # Same single choke point as /unsubscribe -- do not duplicate this logic.
        await asyncio.to_thread(set_org_notification_email_pg, org_id, None)
    inserted = await asyncio.to_thread(
        record_email_suppression_pg, f"{svix_id}#{index}", email, reason
    )
    log.info(
        "resend_webhook.suppressed",
        reason=reason,
        addr=_addr_tag(email),
        orgs_cleared=len(orgs),
        replay=not inserted,
    )
    return len(orgs)


@router.post("")
async def resend_webhook(request: Request) -> dict[str, Any]:
    """Receive a Resend (Svix) webhook. Public route: authenticated by signature only."""
    secret = get_settings().resend_webhook_secret
    if not secret or _decode_secret(secret) is None:
        log.error("resend_webhook.not_configured")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Webhook endpoint not configured on this server."
        )

    body = await _read_capped_body(request)
    if not verify_svix_signature(
        secret=secret,
        svix_id=request.headers.get("svix-id", ""),
        svix_timestamp=request.headers.get("svix-timestamp", ""),
        svix_signature=request.headers.get("svix-signature", ""),
        body=body,
    ):
        log.warning("resend_webhook.signature_rejected")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature.")
    svix_id = request.headers["svix-id"]

    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Expected application/json.")
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Malformed JSON.") from None
    if not isinstance(payload, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Malformed payload.")

    event_type = payload.get("type")
    data = payload.get("data")
    data = data if isinstance(data, dict) else {}
    recipients = _normalise_recipients(data.get("to"))

    reason: str | None = None
    if event_type == "email.complained":
        reason = "complaint"
    elif event_type == "email.bounced":
        bounce = data.get("bounce")
        bounce_type = bounce.get("type") if isinstance(bounce, dict) else None
        if isinstance(bounce_type, str) and bounce_type.lower() == "permanent":
            reason = "hard_bounce"
        else:
            log.info("resend_webhook.soft_bounce_logged", bounce_type=str(bounce_type)[:32])
            return {"status": "logged"}
    elif event_type == "email.delivery_delayed":
        log.info("resend_webhook.delivery_delayed_logged", recipients=len(recipients))
        return {"status": "logged"}

    if reason is None:
        log.debug("resend_webhook.event_ignored", event_type=str(event_type)[:64])
        return {"status": "ignored"}

    cleared = 0
    for index, email in enumerate(recipients):
        cleared += await _suppress(svix_id, index, email, reason)
    return {"status": "processed", "suppressed": len(recipients), "orgs_cleared": cleared}
