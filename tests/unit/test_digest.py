from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from app.core.alerts.channels.base import ChannelError
from app.core.alerts.channels.fake import FakeChannel
from app.core.alerts.digest import (
    PendingDigestEvent,
    build_digest_email,
    collect_pending_for_org,
    run_digest_for_org,
)
from app.core.alerts.rules import AlertEventType

# ---------------------------------------------------------------------------
# Factory helpers — storage functions return plain dicts, not Pydantic objects.
# ---------------------------------------------------------------------------

_ORG_CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def make_extraction_row(
    input_hash: str = "hash1",
    product: str = "Test Product",
    urgency: str = "high",
    topics: list[str] | None = None,
    cons: list[str] | None = None,
    created_at: datetime | None = None,
) -> dict[str, object]:
    return {
        "input_hash": input_hash,
        "product": product,
        "urgency": urgency,
        "topics": topics or ["shipping"],
        "cons": cons or ["broke fast"],
        "created_at": created_at or datetime(2026, 1, 2, tzinfo=UTC),
    }


def make_audit_row(
    review_hash: str = "rhash1",
    score: float = 0.1,
    label: str = "likely_fake",
    created_at: datetime | None = None,
) -> dict[str, object]:
    return {
        "review_hash": review_hash,
        "score": score,
        "label": label,
        "flags": [],
        "created_at": created_at or datetime(2026, 1, 2, tzinfo=UTC),
    }


_DAILY_DIGEST_PREF = {"event_type": "irrelevant", "enabled": True, "frequency": "daily_digest"}


class ErrorChannel:
    async def send(self, message: object) -> None:
        raise ChannelError("delivery failed")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_drop_all_pending_events_collected() -> None:
    extraction_rows = [
        make_extraction_row(input_hash="h1"),
        make_extraction_row(input_hash="h2"),
    ]
    audit_rows = [make_audit_row(review_hash="r1")]

    with (
        patch("app.core.alerts.digest.get_preference_pg", MagicMock(return_value=_DAILY_DIGEST_PREF)),
        patch("app.core.alerts.digest.get_last_digest_watermark_pg", MagicMock(return_value=_ORG_CREATED_AT)),
        patch("app.core.alerts.digest.get_org_created_at_pg", MagicMock(return_value=_ORG_CREATED_AT)),
        patch("app.core.alerts.digest.list_extractions_since_pg", MagicMock(return_value=extraction_rows)),
        patch("app.core.alerts.digest.list_authenticity_audits_since_pg", MagicMock(return_value=audit_rows)),
        patch("app.core.alerts.digest.is_already_alerted_pg", MagicMock(return_value=False)),
    ):
        result = await collect_pending_for_org("org1")

    assert len(result) == 3
    high_urgency = [pe for pe in result if pe.event.event_type == AlertEventType.HIGH_URGENCY]
    likely_fake = [pe for pe in result if pe.event.event_type == AlertEventType.LIKELY_FAKE]
    assert {pe.review_id for pe in high_urgency} == {"h1", "h2"}
    assert {pe.review_id for pe in likely_fake} == {"r1"}


@pytest.mark.asyncio
async def test_run_digest_sends_one_email_and_records_all_events() -> None:
    extraction_rows = [
        make_extraction_row(input_hash="h1"),
        make_extraction_row(input_hash="h2"),
    ]
    audit_rows = [make_audit_row(review_hash="r1")]
    fake = FakeChannel()

    with (
        patch("app.core.alerts.digest.get_preference_pg", MagicMock(return_value=_DAILY_DIGEST_PREF)),
        patch("app.core.alerts.digest.get_last_digest_watermark_pg", MagicMock(return_value=_ORG_CREATED_AT)),
        patch("app.core.alerts.digest.get_org_created_at_pg", MagicMock(return_value=_ORG_CREATED_AT)),
        patch("app.core.alerts.digest.list_extractions_since_pg", MagicMock(return_value=extraction_rows)),
        patch("app.core.alerts.digest.list_authenticity_audits_since_pg", MagicMock(return_value=audit_rows)),
        patch("app.core.alerts.digest.is_already_alerted_pg", MagicMock(return_value=False)),
        patch("app.core.alerts.digest.get_org_notification_email_pg", MagicMock(return_value="seller@example.com")),
        patch("app.core.alerts.digest.record_alert_sent_pg", MagicMock(return_value=None)) as mock_record,
    ):
        result = await run_digest_for_org("org1", fake)

    assert len(fake.sent) == 1
    assert mock_record.call_count == 3
    assert len(result) == 3


@pytest.mark.asyncio
async def test_dedupe_second_sweep_sends_nothing() -> None:
    extraction_rows = [
        make_extraction_row(input_hash="h1"),
        make_extraction_row(input_hash="h2"),
    ]
    audit_rows = [make_audit_row(review_hash="r1")]

    with (
        patch("app.core.alerts.digest.get_preference_pg", MagicMock(return_value=_DAILY_DIGEST_PREF)),
        patch("app.core.alerts.digest.get_last_digest_watermark_pg", MagicMock(return_value=_ORG_CREATED_AT)),
        patch("app.core.alerts.digest.get_org_created_at_pg", MagicMock(return_value=_ORG_CREATED_AT)),
        patch("app.core.alerts.digest.list_extractions_since_pg", MagicMock(return_value=extraction_rows)),
        patch("app.core.alerts.digest.list_authenticity_audits_since_pg", MagicMock(return_value=audit_rows)),
        patch("app.core.alerts.digest.is_already_alerted_pg", MagicMock(return_value=True)),
    ):
        result = await collect_pending_for_org("org1")

    assert result == []


@pytest.mark.asyncio
async def test_empty_digest_sends_nothing() -> None:
    fake = FakeChannel()
    with (
        patch("app.core.alerts.digest.get_preference_pg", MagicMock(return_value=_DAILY_DIGEST_PREF)),
        patch("app.core.alerts.digest.get_last_digest_watermark_pg", MagicMock(return_value=_ORG_CREATED_AT)),
        patch("app.core.alerts.digest.get_org_created_at_pg", MagicMock(return_value=_ORG_CREATED_AT)),
        patch("app.core.alerts.digest.list_extractions_since_pg", MagicMock(return_value=[])),
        patch("app.core.alerts.digest.list_authenticity_audits_since_pg", MagicMock(return_value=[])),
        patch("app.core.alerts.digest.is_already_alerted_pg", MagicMock(return_value=False)),
        patch("app.core.alerts.digest.record_alert_sent_pg", MagicMock(return_value=None)) as mock_record,
    ):
        collected = await collect_pending_for_org("org1")
        result = await run_digest_for_org("org1", fake)

    assert collected == []
    assert result == []
    assert len(fake.sent) == 0
    mock_record.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_preference_excluded() -> None:
    def pref_side_effect(org_id: str, event_type: str) -> dict[str, object] | None:
        if event_type == "high_urgency":
            return {"event_type": "high_urgency", "enabled": False, "frequency": "daily_digest"}
        return {"event_type": "likely_fake", "enabled": True, "frequency": "daily_digest"}

    extraction_rows = [make_extraction_row(input_hash="h1")]
    audit_rows = [make_audit_row(review_hash="r1")]

    with (
        patch("app.core.alerts.digest.get_preference_pg", MagicMock(side_effect=pref_side_effect)),
        patch("app.core.alerts.digest.get_last_digest_watermark_pg", MagicMock(return_value=_ORG_CREATED_AT)),
        patch("app.core.alerts.digest.get_org_created_at_pg", MagicMock(return_value=_ORG_CREATED_AT)),
        patch("app.core.alerts.digest.list_extractions_since_pg", MagicMock(return_value=extraction_rows)),
        patch("app.core.alerts.digest.list_authenticity_audits_since_pg", MagicMock(return_value=audit_rows)),
        patch("app.core.alerts.digest.is_already_alerted_pg", MagicMock(return_value=False)),
    ):
        result = await collect_pending_for_org("org1")

    assert all(pe.event.event_type != AlertEventType.HIGH_URGENCY for pe in result)
    assert any(pe.event.event_type == AlertEventType.LIKELY_FAKE for pe in result)


@pytest.mark.asyncio
async def test_immediate_frequency_excluded_unaffected() -> None:
    def pref_side_effect(org_id: str, event_type: str) -> dict[str, object] | None:
        if event_type == "high_urgency":
            return {"event_type": "high_urgency", "enabled": True, "frequency": "immediate"}
        return {"event_type": "likely_fake", "enabled": True, "frequency": "immediate"}

    extraction_rows = [make_extraction_row(input_hash="h1")]

    with (
        patch("app.core.alerts.digest.get_preference_pg", MagicMock(side_effect=pref_side_effect)),
        patch("app.core.alerts.digest.get_last_digest_watermark_pg", MagicMock(return_value=_ORG_CREATED_AT)),
        patch("app.core.alerts.digest.get_org_created_at_pg", MagicMock(return_value=_ORG_CREATED_AT)),
        patch("app.core.alerts.digest.list_extractions_since_pg", MagicMock(return_value=extraction_rows)),
        patch("app.core.alerts.digest.list_authenticity_audits_since_pg", MagicMock(return_value=[])),
        patch("app.core.alerts.digest.is_already_alerted_pg", MagicMock(return_value=False)),
    ):
        result = await collect_pending_for_org("org1")

    assert result == []


@pytest.mark.asyncio
async def test_no_recipient_email_sends_nothing_and_does_not_record() -> None:
    extraction_rows = [make_extraction_row(input_hash="h1")]
    fake = FakeChannel()

    with (
        patch("app.core.alerts.digest.get_preference_pg", MagicMock(return_value=_DAILY_DIGEST_PREF)),
        patch("app.core.alerts.digest.get_last_digest_watermark_pg", MagicMock(return_value=_ORG_CREATED_AT)),
        patch("app.core.alerts.digest.get_org_created_at_pg", MagicMock(return_value=_ORG_CREATED_AT)),
        patch("app.core.alerts.digest.list_extractions_since_pg", MagicMock(return_value=extraction_rows)),
        patch("app.core.alerts.digest.list_authenticity_audits_since_pg", MagicMock(return_value=[])),
        patch("app.core.alerts.digest.is_already_alerted_pg", MagicMock(return_value=False)),
        patch("app.core.alerts.digest.get_org_notification_email_pg", MagicMock(return_value=None)),
        patch("app.core.alerts.digest.record_alert_sent_pg", MagicMock(return_value=None)) as mock_record,
    ):
        result = await run_digest_for_org("org1", fake)

    assert result == []
    assert len(fake.sent) == 0
    mock_record.assert_not_called()


@pytest.mark.asyncio
async def test_send_failure_does_not_record_preserves_retry() -> None:
    extraction_rows = [make_extraction_row(input_hash="h1")]
    error_channel = ErrorChannel()

    with (
        patch("app.core.alerts.digest.get_preference_pg", MagicMock(return_value=_DAILY_DIGEST_PREF)),
        patch("app.core.alerts.digest.get_last_digest_watermark_pg", MagicMock(return_value=_ORG_CREATED_AT)),
        patch("app.core.alerts.digest.get_org_created_at_pg", MagicMock(return_value=_ORG_CREATED_AT)),
        patch("app.core.alerts.digest.list_extractions_since_pg", MagicMock(return_value=extraction_rows)),
        patch("app.core.alerts.digest.list_authenticity_audits_since_pg", MagicMock(return_value=[])),
        patch("app.core.alerts.digest.is_already_alerted_pg", MagicMock(return_value=False)),
        patch("app.core.alerts.digest.get_org_notification_email_pg", MagicMock(return_value="seller@example.com")),
        patch("app.core.alerts.digest.record_alert_sent_pg", MagicMock(return_value=None)) as mock_record,
    ):
        result = await run_digest_for_org("org1", error_channel)  # type: ignore[arg-type]

    assert result == []
    mock_record.assert_not_called()


@pytest.mark.asyncio
async def test_watermark_fallback_to_org_created_at() -> None:
    with (
        patch("app.core.alerts.digest.get_preference_pg", MagicMock(return_value=_DAILY_DIGEST_PREF)),
        patch("app.core.alerts.digest.get_last_digest_watermark_pg", MagicMock(return_value=None)),
        patch("app.core.alerts.digest.get_org_created_at_pg", MagicMock(return_value=_ORG_CREATED_AT)) as mock_created_at,
        patch("app.core.alerts.digest.list_extractions_since_pg", MagicMock(return_value=[])),
        patch("app.core.alerts.digest.list_authenticity_audits_since_pg", MagicMock(return_value=[])),
        patch("app.core.alerts.digest.is_already_alerted_pg", MagicMock(return_value=False)),
    ):
        await collect_pending_for_org("org1")

    mock_created_at.assert_called()


def test_build_digest_email_empty_returns_none() -> None:
    assert build_digest_email("org1", "seller@example.com", []) is None


def test_build_digest_email_nonempty_has_subject_and_events() -> None:
    from app.core.alerts.rules import AlertEvent

    events = [
        PendingDigestEvent(
            review_id="h1",
            event=AlertEvent(
                event_type=AlertEventType.HIGH_URGENCY,
                details={"topics": ["shipping"], "cons": ["broke"]},
            ),
        )
    ]
    message = build_digest_email("org1", "seller@example.com", events)
    assert message is not None
    assert "1 event" in message.subject
    assert "h1" in message.body_text
