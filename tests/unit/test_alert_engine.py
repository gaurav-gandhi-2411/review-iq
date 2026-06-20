from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from app.core.alerts.channels.base import ChannelError
from app.core.alerts.channels.fake import FakeChannel
from app.core.alerts.engine import evaluate_and_alert
from app.core.alerts.rules import AlertEventType
from app.core.authenticity.schema import AuthenticityLabel, AuthenticityResult
from app.core.schemas import ReviewExtraction, Urgency


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def make_extraction(
    urgency: Urgency = Urgency.low,
    topics: list[str] | None = None,
    cons: list[str] | None = None,
) -> ReviewExtraction:
    return ReviewExtraction(
        product="Test Product",
        urgency=urgency,
        topics=topics or [],
        cons=cons or [],
    )


def make_auth(
    label: AuthenticityLabel = AuthenticityLabel.GENUINE,
    score: float = 0.9,
) -> AuthenticityResult:
    return AuthenticityResult(
        score=score,
        label=label,
        review_hash="abc123",
        scored_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Error channel for test_channel_error_does_not_propagate
# ---------------------------------------------------------------------------


class ErrorChannel:
    async def send(self, message: object) -> None:
        raise ChannelError("delivery failed")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_high_urgency_sends_one_alert() -> None:
    fake = FakeChannel()
    with (
        patch("app.core.alerts.engine.is_already_alerted_pg", MagicMock(return_value=False)),
        patch("app.core.alerts.engine.get_preference_pg", MagicMock(return_value=None)),
        patch("app.core.alerts.engine.record_alert_sent_pg", MagicMock(return_value=None)) as mock_record,
        patch("app.core.alerts.engine.get_org_notification_email_pg", MagicMock(return_value="seller@example.com")),
    ):
        result = await evaluate_and_alert(
            org_id="org1",
            review_id="rev1",
            extraction=make_extraction(urgency=Urgency.high),
            auth=make_auth(),
            channel=fake,
            recipient_email="seller@example.com",
        )

    assert len(fake.sent) == 1
    assert fake.sent[0].event.event_type == AlertEventType.HIGH_URGENCY
    assert fake.sent[0].recipient_email == "seller@example.com"
    mock_record.assert_called_once()


@pytest.mark.asyncio
async def test_duplicate_review_not_alerted_twice() -> None:
    fake = FakeChannel()
    with (
        patch("app.core.alerts.engine.is_already_alerted_pg", MagicMock(return_value=True)),
        patch("app.core.alerts.engine.get_preference_pg", MagicMock(return_value=None)),
        patch("app.core.alerts.engine.record_alert_sent_pg", MagicMock(return_value=None)) as mock_record,
        patch("app.core.alerts.engine.get_org_notification_email_pg", MagicMock(return_value="seller@example.com")),
    ):
        await evaluate_and_alert(
            org_id="org1",
            review_id="rev1",
            extraction=make_extraction(urgency=Urgency.high),
            auth=make_auth(),
            channel=fake,
            recipient_email="seller@example.com",
        )

    assert len(fake.sent) == 0
    mock_record.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_event_type_not_alerted() -> None:
    fake = FakeChannel()
    with (
        patch("app.core.alerts.engine.is_already_alerted_pg", MagicMock(return_value=False)),
        patch(
            "app.core.alerts.engine.get_preference_pg",
            MagicMock(return_value={"event_type": "high_urgency", "enabled": False, "frequency": "immediate"}),
        ),
        patch("app.core.alerts.engine.record_alert_sent_pg", MagicMock(return_value=None)),
        patch("app.core.alerts.engine.get_org_notification_email_pg", MagicMock(return_value="seller@example.com")),
    ):
        await evaluate_and_alert(
            org_id="org1",
            review_id="rev1",
            extraction=make_extraction(urgency=Urgency.high),
            auth=make_auth(),
            channel=fake,
            recipient_email="seller@example.com",
        )

    assert len(fake.sent) == 0


@pytest.mark.asyncio
async def test_daily_digest_frequency_skips_immediate_send() -> None:
    fake = FakeChannel()
    with (
        patch("app.core.alerts.engine.is_already_alerted_pg", MagicMock(return_value=False)),
        patch(
            "app.core.alerts.engine.get_preference_pg",
            MagicMock(return_value={"event_type": "high_urgency", "enabled": True, "frequency": "daily_digest"}),
        ),
        patch("app.core.alerts.engine.record_alert_sent_pg", MagicMock(return_value=None)) as mock_record,
        patch("app.core.alerts.engine.get_org_notification_email_pg", MagicMock(return_value="seller@example.com")),
    ):
        await evaluate_and_alert(
            org_id="org1",
            review_id="rev1",
            extraction=make_extraction(urgency=Urgency.high),
            auth=make_auth(),
            channel=fake,
            recipient_email="seller@example.com",
        )

    assert len(fake.sent) == 0
    mock_record.assert_not_called()


@pytest.mark.asyncio
async def test_likely_fake_sends_alert() -> None:
    fake = FakeChannel()
    with (
        patch("app.core.alerts.engine.is_already_alerted_pg", MagicMock(return_value=False)),
        patch("app.core.alerts.engine.get_preference_pg", MagicMock(return_value=None)),
        patch("app.core.alerts.engine.record_alert_sent_pg", MagicMock(return_value=None)),
        patch("app.core.alerts.engine.get_org_notification_email_pg", MagicMock(return_value="seller@example.com")),
    ):
        await evaluate_and_alert(
            org_id="org1",
            review_id="rev1",
            extraction=make_extraction(urgency=Urgency.low),
            auth=make_auth(label=AuthenticityLabel.LIKELY_FAKE, score=0.1),
            channel=fake,
            recipient_email="seller@example.com",
        )

    assert len(fake.sent) == 1
    assert fake.sent[0].event.event_type == AlertEventType.LIKELY_FAKE


@pytest.mark.asyncio
async def test_high_urgency_and_likely_fake_both_fire() -> None:
    fake = FakeChannel()
    with (
        patch("app.core.alerts.engine.is_already_alerted_pg", MagicMock(side_effect=[False, False])),
        patch("app.core.alerts.engine.get_preference_pg", MagicMock(side_effect=[None, None])),
        patch("app.core.alerts.engine.record_alert_sent_pg", MagicMock(return_value=None)),
        patch("app.core.alerts.engine.get_org_notification_email_pg", MagicMock(return_value="seller@example.com")),
    ):
        await evaluate_and_alert(
            org_id="org1",
            review_id="rev1",
            extraction=make_extraction(urgency=Urgency.high),
            auth=make_auth(label=AuthenticityLabel.LIKELY_FAKE, score=0.1),
            channel=fake,
            recipient_email="seller@example.com",
        )

    assert len(fake.sent) == 2
    assert fake.sent[0].event.event_type == AlertEventType.HIGH_URGENCY
    assert fake.sent[1].event.event_type == AlertEventType.LIKELY_FAKE


@pytest.mark.asyncio
async def test_no_recipient_email_no_send() -> None:
    fake = FakeChannel()
    with (
        patch("app.core.alerts.engine.get_org_notification_email_pg", MagicMock(return_value=None)),
        patch("app.core.alerts.engine.is_already_alerted_pg", MagicMock(return_value=False)),
        patch("app.core.alerts.engine.get_preference_pg", MagicMock(return_value=None)),
        patch("app.core.alerts.engine.record_alert_sent_pg", MagicMock(return_value=None)) as mock_record,
    ):
        await evaluate_and_alert(
            org_id="org1",
            review_id="rev1",
            extraction=make_extraction(urgency=Urgency.high),
            auth=make_auth(),
            channel=fake,
            # no recipient_email passed → falls back to get_org_notification_email_pg → None
        )

    assert len(fake.sent) == 0
    mock_record.assert_not_called()


@pytest.mark.asyncio
async def test_low_urgency_genuine_no_events() -> None:
    fake = FakeChannel()
    with (
        patch("app.core.alerts.engine.get_org_notification_email_pg", MagicMock()) as mock_email,
        patch("app.core.alerts.engine.is_already_alerted_pg", MagicMock()) as mock_dedup,
        patch("app.core.alerts.engine.get_preference_pg", MagicMock()) as mock_pref,
        patch("app.core.alerts.engine.record_alert_sent_pg", MagicMock()) as mock_record,
    ):
        result = await evaluate_and_alert(
            org_id="org1",
            review_id="rev1",
            extraction=make_extraction(urgency=Urgency.low),
            auth=make_auth(label=AuthenticityLabel.GENUINE, score=0.9),
            channel=fake,
            recipient_email="seller@example.com",
        )

    assert result == []
    assert len(fake.sent) == 0
    mock_email.assert_not_called()
    mock_dedup.assert_not_called()
    mock_pref.assert_not_called()
    mock_record.assert_not_called()


@pytest.mark.asyncio
async def test_channel_error_does_not_propagate() -> None:
    error_channel = ErrorChannel()
    with (
        patch("app.core.alerts.engine.is_already_alerted_pg", MagicMock(return_value=False)),
        patch("app.core.alerts.engine.get_preference_pg", MagicMock(return_value=None)),
        patch("app.core.alerts.engine.record_alert_sent_pg", MagicMock(return_value=None)),
        patch("app.core.alerts.engine.get_org_notification_email_pg", MagicMock(return_value="seller@example.com")),
    ):
        result = await evaluate_and_alert(
            org_id="org1",
            review_id="rev1",
            extraction=make_extraction(urgency=Urgency.high),
            auth=make_auth(),
            channel=error_channel,  # type: ignore[arg-type]
            recipient_email="seller@example.com",
        )

    assert result == []


@pytest.mark.asyncio
async def test_message_subject_contains_event_info() -> None:
    fake = FakeChannel()
    with (
        patch("app.core.alerts.engine.is_already_alerted_pg", MagicMock(return_value=False)),
        patch("app.core.alerts.engine.get_preference_pg", MagicMock(return_value=None)),
        patch("app.core.alerts.engine.record_alert_sent_pg", MagicMock(return_value=None)),
        patch("app.core.alerts.engine.get_org_notification_email_pg", MagicMock(return_value="seller@example.com")),
    ):
        await evaluate_and_alert(
            org_id="org1",
            review_id="rev1",
            extraction=make_extraction(urgency=Urgency.high),
            auth=make_auth(),
            channel=fake,
            recipient_email="seller@example.com",
        )

    assert len(fake.sent) == 1
    assert "Urgent" in fake.sent[0].subject
