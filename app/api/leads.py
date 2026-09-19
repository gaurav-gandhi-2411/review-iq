"""Public lead-capture endpoint for the marketing site -- POST /leads (Session 15c C9).

Replaces the marketing site's two mailto: CTAs. Unauthenticated by design (a visitor is
not a customer yet), so every defence is on this route: strict validation, a honeypot,
per-IP + global rate limits, and email that can never fail the request.

Contract (the marketing-site form is built against exactly this):
  POST /leads  Content-Type: application/json
  body {name, email, company, brands, reviews_per_month, message?, website}
  200 {"ok": true}
  422 {"ok": false, "error": "<machine code>", "message": "<actionable text>"}
  429 {"ok": false, "error": "rate_limited", "message": "..."}
  503 {"ok": false, "error": "temporarily_unavailable", "message": "..."} -- only when the
      lead could be neither stored nor emailed, i.e. it would otherwise be silently lost.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import unicodedata
import uuid
from typing import Any

import resend
import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from pydantic_core import PydanticCustomError
from slowapi.util import get_remote_address

from app.core.config import get_settings
from app.core.metrics import LEADS_TOTAL
from app.core.rate_limit import limiter
from app.core.storage_pg import insert_lead_pg, update_lead_email_status_pg

router = APIRouter(tags=["leads"])
log = structlog.get_logger(__name__)

LEADS_PATH = "/leads"

# Origins allowed to call this route cross-origin (wired in app/main.py through
# app.core.cors.PathScopedCORSMiddleware -- NOT added to the global allowlist).
LEADS_ALLOWED_ORIGINS = ["https://samidhareviews.xyz", "https://www.samidhareviews.xyz"]

# A real submission is well under 2 KB; 16 KB leaves room for a 4,000-char message in
# multi-byte scripts (Hindi/Devanagari is 3 bytes/char in UTF-8) plus JSON overhead.
_MAX_BODY_BYTES = 16 * 1024

# Rate limits. Chosen against real numbers, not vibes:
#  - 5/hour per IP: a genuine visitor submits once, maybe twice after fixing a typo;
#    5/hour leaves headroom for a shared office/NAT IP without letting one host spam.
#  - 10/hour and 30/day GLOBAL (one shared key for every caller): each accepted lead
#    sends 2 emails, and Resend's free tier is 100 emails/day shared with the alert
#    emails -- 30 leads/day = 60 emails leaves headroom for alerts, and bounds a botnet
#    (which the per-IP limit cannot) to at most 30 messages/day into hello@. The cost is
#    that a flood can crowd out real leads; the 429 message tells them to email directly.
#  - Counted BEFORE validation and before the honeypot check, so bots probing the form
#    burn the same budget as real traffic. In-process storage: with Cloud Run maxScale=3
#    the effective ceilings can be up to ~3x these (same documented limitation as
#    app/core/rate_limit.py).
_PER_IP_LIMIT = "5/hour"
_GLOBAL_BURST_LIMIT = "10/hour"
_GLOBAL_DAILY_LIMIT = "30/day"

_EMAIL_SEND_TIMEOUT_S = 10.0
_MAX_USER_AGENT_CHARS = 256

# Local part: RFC 5322 atext characters; domain: LDH labels, at least one dot. ASCII only
# (IDN/internationalised addresses are rejected with an actionable message rather than
# risking a malformed From/To downstream). No email-validator dependency in this repo.
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


def _global_key(request: Request) -> str:  # noqa: ARG001 -- slowapi requires the `request` name
    """One shared slowapi key so the cap counts every caller together."""
    return "leads-global"


def _clean_text(value: Any, *, field: str, max_len: int, multiline: bool = False) -> str:
    """Strip, reject non-strings and control/format characters, enforce the length cap."""
    if not isinstance(value, str):
        raise PydanticCustomError("invalid_field", f"{field} must be a string.")
    value = value.strip()
    if multiline:
        value = value.replace("\r\n", "\n")
    for ch in value:
        cat = unicodedata.category(ch)
        if multiline and ch in ("\n", "\t"):
            continue
        # Cc: C0/C1 controls (incl. CR, LF, NUL) -- the header-injection class.
        # Zl/Zp: U+2028/U+2029 act as line breaks. Cs/Co/Cn: surrogates, private use,
        # unassigned. Cf (format chars: zero-width, bidi overrides) is rejected in
        # single-line fields, where it can only be used to spoof a name or subject;
        # tolerated in `message` so emoji ZWJ sequences survive.
        if cat in ("Cc", "Zl", "Zp", "Cs", "Co", "Cn") or (cat == "Cf" and not multiline):
            raise PydanticCustomError(
                "invalid_characters", f"{field} contains characters that are not allowed."
            )
    if len(value) > max_len:
        raise PydanticCustomError(
            "field_too_long", f"{field} must be at most {max_len} characters."
        )
    return value


class LeadRequest(BaseModel):
    """Validated lead submission. The honeypot (`website`) is handled before this model."""

    model_config = ConfigDict(extra="ignore")

    name: str
    email: str
    company: str
    brands: str
    reviews_per_month: str
    message: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _v_name(cls, v: Any) -> str:
        return cls._required(v, "name", 100)

    @field_validator("company", mode="before")
    @classmethod
    def _v_company(cls, v: Any) -> str:
        return cls._required(v, "company", 150)

    @field_validator("brands", mode="before")
    @classmethod
    def _v_brands(cls, v: Any) -> str:
        return cls._required(v, "brands", 500)

    @field_validator("reviews_per_month", mode="before")
    @classmethod
    def _v_rpm(cls, v: Any) -> str:
        return cls._required(v, "reviews_per_month", 50)

    @field_validator("email", mode="before")
    @classmethod
    def _v_email(cls, v: Any) -> str:
        value = _clean_text(v, field="email", max_len=254)
        if not value:
            raise PydanticCustomError("field_required", "email is required.")
        local = value.partition("@")[0]
        if not _EMAIL_RE.match(value) or len(local) > 64:
            raise PydanticCustomError(
                "invalid_email", "email must be a valid address such as name@company.com."
            )
        return value.lower()

    @field_validator("message", mode="before")
    @classmethod
    def _v_message(cls, v: Any) -> str | None:
        if v is None:
            return None
        value = _clean_text(v, field="message", max_len=4000, multiline=True)
        return value or None

    @staticmethod
    def _required(v: Any, field: str, max_len: int) -> str:
        value = _clean_text(v, field=field, max_len=max_len)
        if not value:
            raise PydanticCustomError("field_required", f"{field} is required.")
        return value


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content={"ok": False, "error": code, "message": message}
    )


def leads_rate_limit_response() -> JSONResponse:
    """The 429 body in the /leads contract shape (used by main.py's rate-limit handler)."""
    notify = get_settings().leads_notify_email
    return JSONResponse(
        status_code=429,
        content={
            "ok": False,
            "error": "rate_limited",
            "message": (
                "Too many requests. Please try again in a little while, or email "
                f"{notify} directly."
            ),
        },
        headers={"Retry-After": "3600"},
    )


def _hash_ip(ip: str) -> str | None:
    """HMAC-SHA256(salt, ip) hex; None when no server secret is configured.

    Never an unkeyed hash: the IPv4 space is small enough to brute-force an unsalted
    SHA-256 in seconds, which would make "we store only a hash" meaningless.
    """
    salt = get_settings().leads_ip_hash_salt
    if not salt or not ip:
        return None
    return hmac.new(salt.encode(), ip.encode(), hashlib.sha256).hexdigest()


def _notification_body(lead: LeadRequest, lead_id: str, persisted: bool) -> str:
    return "\n".join(
        [
            "New lead from the Samidha Reviews marketing site.",
            "",
            f"Name: {lead.name}",
            f"Email: {lead.email}",
            f"Company: {lead.company}",
            f"Brands: {lead.brands}",
            f"Reviews per month: {lead.reviews_per_month}",
            "",
            "Message:",
            lead.message or "(none)",
            "",
            f"Lead id: {lead_id}" + ("" if persisted else " (NOT stored in the database)"),
            "Reply to this email to answer the submitter directly.",
        ]
    )


_CONFIRMATION_BODY = (
    "Thanks for getting in touch with Samidha Reviews.\n\n"
    "We have received your request and will reply to this address. If you need to add "
    "anything, just reply to this email.\n\n"
    "-- The Samidha Reviews team\n"
)


async def _send_email(params: resend.Emails.SendParams, *, kind: str, lead_id: str) -> bool:
    """Send one email via Resend. Never raises: returns False and logs on any failure.

    Bounded by _EMAIL_SEND_TIMEOUT_S (the resend client's own default is 30s, far too long
    to hold a visitor's form submit). Failure behaviour is defined by the caller: a failed
    email degrades email_status, it never fails the request.
    """
    try:
        await asyncio.wait_for(resend.Emails.send_async(params), timeout=_EMAIL_SEND_TIMEOUT_S)
    except Exception as exc:
        # Log the exception type/code only -- str(exc) from Resend can echo the recipient.
        log.warning(
            "leads.email_failed",
            kind=kind,
            lead_id=lead_id,
            error_type=type(exc).__name__,
            error_code=getattr(exc, "code", None),
        )
        return False
    return True


async def _send_lead_emails(lead: LeadRequest, lead_id: str, persisted: bool) -> str:
    """Send the operator notification + submitter confirmation; return the email_status."""
    settings = get_settings()
    if not settings.resend_api_key or not settings.resend_from_email:
        log.warning("leads.email_skipped_unconfigured", lead_id=lead_id)
        return "skipped"

    resend.api_key = settings.resend_api_key
    from_header = (
        f"{settings.resend_from_name} <{settings.resend_from_email}>"
        if settings.resend_from_name
        else settings.resend_from_email
    )
    subject = f"New lead: {lead.company}"[:150]
    notification: resend.Emails.SendParams = {
        "from": from_header,
        "to": [settings.leads_notify_email],
        "reply_to": [lead.email],
        "subject": subject,
        "text": _notification_body(lead, lead_id, persisted),
    }
    confirmation: resend.Emails.SendParams = {
        "from": from_header,
        "to": [lead.email],
        "reply_to": [settings.leads_notify_email],
        "subject": "We received your request - Samidha Reviews",
        "text": _CONFIRMATION_BODY,
    }
    notified, confirmed = await asyncio.gather(
        _send_email(notification, kind="notification", lead_id=lead_id),
        _send_email(confirmation, kind="confirmation", lead_id=lead_id),
    )
    if notified and confirmed:
        return "sent"
    return "partial" if (notified or confirmed) else "failed"


@router.post(
    LEADS_PATH,
    summary="Marketing-site lead capture (unauthenticated, rate-limited)",
    response_model=None,
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["name", "email", "company", "brands", "reviews_per_month"],
                        "properties": {
                            "name": {"type": "string", "maxLength": 100},
                            "email": {"type": "string", "maxLength": 254},
                            "company": {"type": "string", "maxLength": 150},
                            "brands": {"type": "string", "maxLength": 500},
                            "reviews_per_month": {"type": "string", "maxLength": 50},
                            "message": {"type": "string", "maxLength": 4000},
                            "website": {
                                "type": "string",
                                "description": "Honeypot -- must be empty or omitted.",
                            },
                        },
                    },
                }
            }
        }
    },
)
@limiter.limit(_PER_IP_LIMIT)
@limiter.limit(_GLOBAL_BURST_LIMIT, key_func=_global_key)
@limiter.limit(_GLOBAL_DAILY_LIMIT, key_func=_global_key)
async def create_lead(request: Request) -> JSONResponse:
    """Validate, store, and email a marketing-site lead.

    Raw-body parsing (instead of a Pydantic body parameter) is deliberate: it lets the
    honeypot check run BEFORE validation, so a bot that fills `website` gets the same
    200 {"ok": true} whether or not its other fields were valid -- it learns nothing.
    """
    ip = get_remote_address(request)
    ip_hash = _hash_ip(ip)

    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > _MAX_BODY_BYTES:
        LEADS_TOTAL.labels(outcome="invalid").inc()
        return _error(422, "payload_too_large", "The request body is too large.")
    raw = await request.body()
    if len(raw) > _MAX_BODY_BYTES:
        LEADS_TOTAL.labels(outcome="invalid").inc()
        return _error(422, "payload_too_large", "The request body is too large.")

    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        payload = None
    if not isinstance(payload, dict):
        LEADS_TOTAL.labels(outcome="invalid").inc()
        return _error(422, "invalid_json", "The request body must be a JSON object.")

    honeypot = payload.get("website")
    if honeypot not in (None, "") and str(honeypot).strip() != "":
        # Same response as success; persist and email nothing.
        LEADS_TOTAL.labels(outcome="honeypot").inc()
        log.info("leads.honeypot", ip_hash=ip_hash[:12] if ip_hash else None)
        return JSONResponse(status_code=200, content={"ok": True})

    try:
        lead = LeadRequest.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first["loc"])
        code = first["type"]
        if code == "missing":
            code, message = "field_required", f"{field} is required."
        elif code in {
            "field_required",
            "invalid_email",
            "field_too_long",
            "invalid_characters",
            "invalid_field",
        }:
            message = first["msg"]
        else:
            code, message = "invalid_field", f"{field} is invalid."
        LEADS_TOTAL.labels(outcome="invalid").inc()
        return _error(422, code, message)

    lead_id = str(uuid.uuid4())
    user_agent = _clean_user_agent(request.headers.get("user-agent"))

    persisted = True
    try:
        await asyncio.to_thread(
            insert_lead_pg,
            lead_id,
            lead.name,
            lead.email,
            lead.company,
            lead.brands,
            lead.reviews_per_month,
            lead.message,
            ip_hash,
            user_agent,
        )
    except Exception as exc:
        persisted = False
        LEADS_TOTAL.labels(outcome="persist_failed").inc()
        log.error(
            "leads.persist_failed",
            lead_id=lead_id,
            error_type=type(exc).__name__,
            ip_hash=ip_hash[:12] if ip_hash else None,
        )

    email_status = await _send_lead_emails(lead, lead_id, persisted)

    if not persisted:
        # Not stored. Only a fully successful send proves hello@ has the lead ("partial"
        # could mean only the confirmation went out), so anything less is an honest 503
        # rather than a false ok:true that would silently lose the lead.
        if email_status == "sent":
            return JSONResponse(status_code=200, content={"ok": True})
        return _error(
            503,
            "temporarily_unavailable",
            "We could not record your request right now. Please try again in a few minutes "
            f"or email {get_settings().leads_notify_email} directly.",
        )

    try:
        updated = await asyncio.to_thread(update_lead_email_status_pg, lead_id, email_status)
        if not updated:
            log.warning("leads.status_update_no_row", lead_id=lead_id, email_status=email_status)
    except Exception as exc:
        log.warning(
            "leads.status_update_failed",
            lead_id=lead_id,
            email_status=email_status,
            error_type=type(exc).__name__,
        )

    if email_status in ("failed", "skipped", "partial"):
        LEADS_TOTAL.labels(outcome="email_degraded").inc()
    LEADS_TOTAL.labels(outcome="accepted").inc()
    log.info("leads.accepted", lead_id=lead_id, email_status=email_status)
    return JSONResponse(status_code=200, content={"ok": True})


def _clean_user_agent(value: str | None) -> str | None:
    """Truncate and strip control characters; None when absent/empty."""
    if not value:
        return None
    cleaned = "".join(ch for ch in value if unicodedata.category(ch) not in ("Cc", "Zl", "Zp"))
    cleaned = cleaned.strip()[:_MAX_USER_AGENT_CHARS]
    return cleaned or None
