"""Unit tests for ResendChannel's message construction: from-name, reply-to,
and the List-Unsubscribe header — no network calls (resend.Emails.send_async
is mocked)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.core.alerts.channels.base import AlertMessage
from app.core.alerts.channels.resend_channel import ResendChannel
from app.core.alerts.rules import AlertEvent, AlertEventType


def _settings(
    from_name: str = "Review-IQ Alerts",
    reply_to: str = "",
) -> MagicMock:
    s = MagicMock()
    s.resend_api_key = "re_test_key"
    s.resend_from_email = "onboarding@resend.dev"
    s.resend_from_name = from_name
    s.resend_reply_to = reply_to
    return s


def _message(unsubscribe_url: str | None = None) -> AlertMessage:
    event = AlertEvent(event_type=AlertEventType.HIGH_URGENCY, details={})
    return AlertMessage(
        org_id="org-1",
        event=event,
        subject="Test subject",
        body_text="Test body",
        recipient_email="seller@example.com",
        unsubscribe_url=unsubscribe_url,
    )


def _fake_response() -> MagicMock:
    resp = MagicMock()
    resp.id = "msg-123"
    return resp


@pytest.mark.asyncio
async def test_from_header_combines_name_and_email() -> None:
    with patch("app.core.alerts.channels.resend_channel.get_settings", return_value=_settings()):
        channel = ResendChannel()

    with patch(
        "app.core.alerts.channels.resend_channel.resend.Emails.send_async",
        new=AsyncMock(return_value=_fake_response()),
    ) as mock_send:
        await channel.send(_message())

    sent_params = mock_send.call_args[0][0]
    assert sent_params["from"] == "Review-IQ Alerts <onboarding@resend.dev>"
    assert "reply_to" not in sent_params
    assert "headers" not in sent_params


@pytest.mark.asyncio
async def test_from_header_omits_name_when_unset() -> None:
    with patch(
        "app.core.alerts.channels.resend_channel.get_settings",
        return_value=_settings(from_name=""),
    ):
        channel = ResendChannel()

    with patch(
        "app.core.alerts.channels.resend_channel.resend.Emails.send_async",
        new=AsyncMock(return_value=_fake_response()),
    ) as mock_send:
        await channel.send(_message())

    assert mock_send.call_args[0][0]["from"] == "onboarding@resend.dev"


@pytest.mark.asyncio
async def test_reply_to_included_when_configured() -> None:
    with patch(
        "app.core.alerts.channels.resend_channel.get_settings",
        return_value=_settings(reply_to="support@example.com"),
    ):
        channel = ResendChannel()

    with patch(
        "app.core.alerts.channels.resend_channel.resend.Emails.send_async",
        new=AsyncMock(return_value=_fake_response()),
    ) as mock_send:
        await channel.send(_message())

    assert mock_send.call_args[0][0]["reply_to"] == ["support@example.com"]


@pytest.mark.asyncio
async def test_list_unsubscribe_headers_set_when_url_present() -> None:
    with patch("app.core.alerts.channels.resend_channel.get_settings", return_value=_settings()):
        channel = ResendChannel()

    with patch(
        "app.core.alerts.channels.resend_channel.resend.Emails.send_async",
        new=AsyncMock(return_value=_fake_response()),
    ) as mock_send:
        await channel.send(
            _message(unsubscribe_url="https://api.example.com/unsubscribe?org=o&token=t")
        )

    headers = mock_send.call_args[0][0]["headers"]
    assert headers["List-Unsubscribe"] == "<https://api.example.com/unsubscribe?org=o&token=t>"
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


@pytest.mark.asyncio
async def test_no_list_unsubscribe_header_when_url_absent() -> None:
    with patch("app.core.alerts.channels.resend_channel.get_settings", return_value=_settings()):
        channel = ResendChannel()

    with patch(
        "app.core.alerts.channels.resend_channel.resend.Emails.send_async",
        new=AsyncMock(return_value=_fake_response()),
    ) as mock_send:
        await channel.send(_message(unsubscribe_url=None))

    assert "headers" not in mock_send.call_args[0][0]
