from __future__ import annotations

import resend
import structlog

from app.core.alerts.channels.base import AlertMessage, ChannelError
from app.core.config import get_settings

log = structlog.get_logger(__name__)


class ResendChannel:
    """Email delivery via Resend (free tier: 100/day, 3 000/month).

    429 → ChannelError without alert_log record so the event retries on next run.
    Sandbox: onboarding@resend.dev delivers ONLY to the verified Resend account email.
    Real-recipient delivery needs a verified custom domain (deploy-phase debt).
    All failures raise ChannelError — engine catches, logs, continues without crashing.
    """

    def __init__(self) -> None:
        s = get_settings()
        self._api_key = s.resend_api_key
        self._from_email = s.resend_from_email
        self._from_name = s.resend_from_name
        self._reply_to = s.resend_reply_to
        if not self._api_key:
            raise ValueError("RESEND_API_KEY is not configured")
        if not self._from_email:
            raise ValueError("RESEND_FROM_EMAIL is not configured")
        self.last_response: resend.Emails.SendResponse | None = None

    async def send(self, message: AlertMessage) -> None:
        resend.api_key = self._api_key
        from_header = (
            f"{self._from_name} <{self._from_email}>" if self._from_name else self._from_email
        )
        params: resend.Emails.SendParams = {
            "from": from_header,
            "to": [message.recipient_email],
            "subject": message.subject,
            "text": message.body_text,
        }
        if self._reply_to:
            params["reply_to"] = [self._reply_to]
        if message.unsubscribe_url:
            # RFC 8058 one-click unsubscribe: a single HTTPS URL plus the
            # One-Click marker lets mail clients (e.g. Gmail) POST to it
            # automatically without the recipient opening the email.
            params["headers"] = {
                "List-Unsubscribe": f"<{message.unsubscribe_url}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            }
        try:
            response = await resend.Emails.send_async(params)
            self.last_response = response
            log.info(
                "resend.sent",
                message_id=response["id"],
                recipient=message.recipient_email,
                event_type=message.event.event_type,
                org_id=message.org_id,
            )
        except resend.exceptions.RateLimitError as exc:
            log.warning(
                "resend.rate_limited",
                recipient=message.recipient_email,
                event_type=message.event.event_type,
                org_id=message.org_id,
                note="NOT recorded in alert_log — will retry on next run",
            )
            raise ChannelError(f"Resend rate limit (429): {exc}") from exc
        except resend.exceptions.ResendError as exc:
            log.error(
                "resend.send_failed",
                recipient=message.recipient_email,
                event_type=message.event.event_type,
                org_id=message.org_id,
                error_code=getattr(exc, "code", None),
                # exc_info=True renders no traceback text under this app's structlog pipeline
                # (no format_exc_info processor configured) — log the message explicitly so
                # failures are diagnosable from structured logs alone.
                error_message=str(exc),
            )
            raise ChannelError(f"Resend API error ({getattr(exc, 'code', '?')}): {exc}") from exc
        except Exception as exc:
            log.error(
                "resend.send_failed_unexpected",
                recipient=message.recipient_email,
                event_type=message.event.event_type,
                org_id=message.org_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise ChannelError(f"Resend unexpected error: {exc}") from exc
