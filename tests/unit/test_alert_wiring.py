"""Tests proving alert_on_review_event is actually wired into the ingestion pipelines.

evaluate_and_alert() itself is fully covered by tests/unit/test_alert_engine.py — these
tests instead prove the *wiring*: that the three call sites covered here (extraction
funnel cache-hit + fresh, authenticity single cache-hit + fresh, authenticity batch)
actually invoke it, that alert-layer failures never break ingestion, and that
daily_digest-configured event types still don't fire immediately from this path.

CSV ingest's authenticity-alert wiring (the fourth call site) is covered in
tests/unit/test_ingest_worker.py instead — it moved there when the fire-and-forget
_process_ingest_job coroutine was replaced by the durable batch_job_rows worker
path (Option B, 2026-07-09); see app/core/ingest_worker.py::_score_authenticity.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.auth.api_key import ApiKeyContext, require_api_key
from app.core.alerts.channels.base import ChannelError
from app.core.alerts.channels.fake import FakeChannel
from app.core.alerts.rules import AlertEventType
from app.core.authenticity.schema import AuthenticityLabel, AuthenticityResult
from app.core.schemas import ReviewExtractionLLMOutput, ReviewRequest, Sentiment, Urgency
from fastapi.testclient import TestClient

_ORG_ID = str(uuid.uuid4())
_KEY_ID = str(uuid.uuid4())
_USAGE_ID = str(uuid.uuid4())

_CTX = ApiKeyContext(
    org_id=_ORG_ID,
    api_key_id=_KEY_ID,
    key_name="test-key",
    usage_record_id=_USAGE_ID,
)

_REVIEW_TEXT = "This widget broke after one day and support ignored my emails!"


class ErrorChannel:
    """Channel that always fails delivery — simulates Resend being down. Mirrors the
    ErrorChannel defined in tests/unit/test_alert_engine.py."""

    async def send(self, message: object) -> None:
        raise ChannelError("delivery failed")


def _llm_output(urgency: Urgency = Urgency.low) -> ReviewExtractionLLMOutput:
    return ReviewExtractionLLMOutput(
        product="Test Widget",
        stars=1,
        sentiment=Sentiment.negative,
        urgency=urgency,
        topics=["support"],
        competitor_mentions=[],
        pros=[],
        cons=["broken", "no response"],
        language="en",
        confidence=0.9,
    )


def _auth_result(
    label: AuthenticityLabel = AuthenticityLabel.GENUINE, score: float = 0.9, text: str = "x"
) -> AuthenticityResult:
    return AuthenticityResult(
        score=score,
        label=label,
        review_hash=hashlib.sha256(text.encode()).hexdigest(),
        scored_at=datetime.now(UTC),
    )


@contextmanager
def _engine_mocks(fake_channel: FakeChannel, pref: object = None) -> Iterator[None]:
    """Patch the standard alert-engine dependencies used by every wiring test.

    Mirrors test_alert_engine.py's patch style (patch by name in app.core.alerts.engine's
    own namespace) plus a patched _get_default_channel returning a captured FakeChannel.
    """
    with ExitStack() as stack:
        stack.enter_context(
            patch("app.core.alerts.engine.is_already_alerted_pg", MagicMock(return_value=False))
        )
        stack.enter_context(
            patch("app.core.alerts.engine.get_preference_pg", MagicMock(return_value=pref))
        )
        stack.enter_context(
            patch("app.core.alerts.engine.record_alert_sent_pg", MagicMock(return_value=None))
        )
        stack.enter_context(
            patch(
                "app.core.alerts.engine.get_org_notification_email_pg",
                MagicMock(return_value="seller@example.com"),
            )
        )
        stack.enter_context(
            patch(
                "app.core.alerts.engine._get_default_channel",
                MagicMock(return_value=fake_channel),
            )
        )
        yield


@pytest.fixture
def client() -> TestClient:
    """TestClient with require_api_key dependency overridden."""
    from app.main import app

    app.dependency_overrides[require_api_key] = lambda: _CTX
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1-5: extraction funnel wiring (via _run_extraction_v2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_high_urgency_extraction_fires_immediate_alert() -> None:
    """A fresh extraction with urgency=high triggers exactly one immediate alert."""
    from app.api.v2.extract import _run_extraction_v2

    fake = FakeChannel()
    req = ReviewRequest(text=_REVIEW_TEXT)

    with (
        patch("app.api.v2.extract.get_by_hash_pg", return_value=None),
        patch("app.api.v2.extract.save_extraction_pg", return_value=str(uuid.uuid4())),
        patch(
            "app.api.v2.extract.extract_with_llm",
            new=AsyncMock(
                return_value=(_llm_output(urgency=Urgency.high), "mock-model", 42, 150, 80, False)
            ),
        ),
        patch("app.api.v2.extract.update_usage_tokens"),
        patch("app.core.alerts.engine.is_already_alerted_pg", MagicMock(return_value=False)),
        patch("app.core.alerts.engine.get_preference_pg", MagicMock(return_value=None)),
        patch(
            "app.core.alerts.engine.record_alert_sent_pg", MagicMock(return_value=None)
        ) as mock_record,
        patch(
            "app.core.alerts.engine.get_org_notification_email_pg",
            MagicMock(return_value="seller@example.com"),
        ),
        patch("app.core.alerts.engine._get_default_channel", MagicMock(return_value=fake)),
    ):
        result = await _run_extraction_v2(req, _CTX)

    assert result.urgency == Urgency.high
    assert len(fake.sent) == 1
    assert fake.sent[0].event.event_type == AlertEventType.HIGH_URGENCY
    mock_record.assert_called_once()


@pytest.mark.asyncio
async def test_low_urgency_extraction_no_alert() -> None:
    """The default low-urgency fixture does not trigger any alert."""
    from app.api.v2.extract import _run_extraction_v2

    fake = FakeChannel()
    req = ReviewRequest(text=_REVIEW_TEXT)

    with (
        patch("app.api.v2.extract.get_by_hash_pg", return_value=None),
        patch("app.api.v2.extract.save_extraction_pg", return_value=str(uuid.uuid4())),
        patch(
            "app.api.v2.extract.extract_with_llm",
            new=AsyncMock(return_value=(_llm_output(), "mock-model", 42, 150, 80, False)),
        ),
        patch("app.api.v2.extract.update_usage_tokens"),
        _engine_mocks(fake),
    ):
        await _run_extraction_v2(req, _CTX)

    assert len(fake.sent) == 0


@pytest.mark.asyncio
async def test_extraction_dedupe_holds() -> None:
    """A review already recorded in alert_log is not re-alerted even on a fresh urgency=high extraction."""
    from app.api.v2.extract import _run_extraction_v2

    fake = FakeChannel()
    req = ReviewRequest(text=_REVIEW_TEXT)

    with (
        patch("app.api.v2.extract.get_by_hash_pg", return_value=None),
        patch("app.api.v2.extract.save_extraction_pg", return_value=str(uuid.uuid4())),
        patch(
            "app.api.v2.extract.extract_with_llm",
            new=AsyncMock(
                return_value=(_llm_output(urgency=Urgency.high), "mock-model", 42, 150, 80, False)
            ),
        ),
        patch("app.api.v2.extract.update_usage_tokens"),
        patch("app.core.alerts.engine.is_already_alerted_pg", MagicMock(return_value=True)),
        patch("app.core.alerts.engine.get_preference_pg", MagicMock(return_value=None)),
        patch(
            "app.core.alerts.engine.record_alert_sent_pg", MagicMock(return_value=None)
        ) as mock_record,
        patch(
            "app.core.alerts.engine.get_org_notification_email_pg",
            MagicMock(return_value="seller@example.com"),
        ),
        patch("app.core.alerts.engine._get_default_channel", MagicMock(return_value=fake)),
    ):
        await _run_extraction_v2(req, _CTX)

    assert len(fake.sent) == 0
    mock_record.assert_not_called()


@pytest.mark.asyncio
async def test_daily_digest_pref_does_not_fire_immediately() -> None:
    """An org preference of frequency=daily_digest defers the alert — it is the digest batcher's job."""
    from app.api.v2.extract import _run_extraction_v2

    fake = FakeChannel()
    req = ReviewRequest(text=_REVIEW_TEXT)
    digest_pref = {"event_type": "high_urgency", "enabled": True, "frequency": "daily_digest"}

    with (
        patch("app.api.v2.extract.get_by_hash_pg", return_value=None),
        patch("app.api.v2.extract.save_extraction_pg", return_value=str(uuid.uuid4())),
        patch(
            "app.api.v2.extract.extract_with_llm",
            new=AsyncMock(
                return_value=(_llm_output(urgency=Urgency.high), "mock-model", 42, 150, 80, False)
            ),
        ),
        patch("app.api.v2.extract.update_usage_tokens"),
        _engine_mocks(fake, pref=digest_pref),
    ):
        await _run_extraction_v2(req, _CTX)

    assert len(fake.sent) == 0


@pytest.mark.asyncio
async def test_alert_layer_exception_does_not_break_extraction() -> None:
    """A raising alert-layer dependency (simulated DB outage) never breaks _run_extraction_v2."""
    from app.api.v2.extract import _run_extraction_v2
    from app.core.schemas import ReviewExtractionV2

    fake = FakeChannel()
    req = ReviewRequest(text=_REVIEW_TEXT)

    with (
        patch("app.api.v2.extract.get_by_hash_pg", return_value=None),
        patch("app.api.v2.extract.save_extraction_pg", return_value=str(uuid.uuid4())),
        patch(
            "app.api.v2.extract.extract_with_llm",
            new=AsyncMock(
                return_value=(_llm_output(urgency=Urgency.high), "mock-model", 42, 150, 80, False)
            ),
        ),
        patch("app.api.v2.extract.update_usage_tokens"),
        patch("app.core.alerts.engine.is_already_alerted_pg", MagicMock(return_value=False)),
        patch(
            "app.core.alerts.engine.get_preference_pg",
            MagicMock(side_effect=RuntimeError("db down")),
        ),
        patch("app.core.alerts.engine.record_alert_sent_pg", MagicMock(return_value=None)),
        patch(
            "app.core.alerts.engine.get_org_notification_email_pg",
            MagicMock(return_value="seller@example.com"),
        ),
        patch("app.core.alerts.engine._get_default_channel", MagicMock(return_value=fake)),
    ):
        result = await _run_extraction_v2(req, _CTX)

    assert isinstance(result, ReviewExtractionV2)
    assert len(fake.sent) == 0


# ---------------------------------------------------------------------------
# 6-7: authenticity single wiring (via TestClient -> POST /v2/authenticity)
# ---------------------------------------------------------------------------


def test_likely_fake_authenticity_fires_immediate_alert(client: TestClient) -> None:
    """A LIKELY_FAKE authenticity result triggers exactly one immediate alert."""
    fake_channel = FakeChannel()
    fake_result = _auth_result(label=AuthenticityLabel.LIKELY_FAKE, score=0.1, text="fake review")

    with (
        patch(
            "app.api.v2.authenticity.get_authenticity_audit_by_hash_pg",
            new=MagicMock(return_value=None),
        ),
        patch(
            "app.api.v2.authenticity.engine.score_single", new=AsyncMock(return_value=fake_result)
        ),
        patch(
            "app.api.v2.authenticity.save_authenticity_audit_pg", new=MagicMock(return_value=None)
        ),
        patch("app.core.alerts.engine.is_already_alerted_pg", MagicMock(return_value=False)),
        patch("app.core.alerts.engine.get_preference_pg", MagicMock(return_value=None)),
        patch("app.core.alerts.engine.record_alert_sent_pg", MagicMock(return_value=None)),
        patch(
            "app.core.alerts.engine.get_org_notification_email_pg",
            MagicMock(return_value="seller@example.com"),
        ),
        patch("app.core.alerts.engine._get_default_channel", MagicMock(return_value=fake_channel)),
    ):
        resp = client.post("/v2/authenticity", json={"text": "fake review", "stars": 5})

    assert resp.status_code == 200
    assert len(fake_channel.sent) == 1
    assert fake_channel.sent[0].event.event_type == AlertEventType.LIKELY_FAKE


def test_genuine_authenticity_no_alert(client: TestClient) -> None:
    """A GENUINE authenticity result triggers no alert."""
    fake_channel = FakeChannel()
    genuine_result = _auth_result(label=AuthenticityLabel.GENUINE, score=0.9, text="great product")

    with (
        patch(
            "app.api.v2.authenticity.get_authenticity_audit_by_hash_pg",
            new=MagicMock(return_value=None),
        ),
        patch(
            "app.api.v2.authenticity.engine.score_single",
            new=AsyncMock(return_value=genuine_result),
        ),
        patch(
            "app.api.v2.authenticity.save_authenticity_audit_pg", new=MagicMock(return_value=None)
        ),
        patch("app.core.alerts.engine.is_already_alerted_pg", MagicMock(return_value=False)),
        patch("app.core.alerts.engine.get_preference_pg", MagicMock(return_value=None)),
        patch("app.core.alerts.engine.record_alert_sent_pg", MagicMock(return_value=None)),
        patch(
            "app.core.alerts.engine.get_org_notification_email_pg",
            MagicMock(return_value="seller@example.com"),
        ),
        patch("app.core.alerts.engine._get_default_channel", MagicMock(return_value=fake_channel)),
    ):
        resp = client.post("/v2/authenticity", json={"text": "great product", "stars": 5})

    assert resp.status_code == 200
    assert len(fake_channel.sent) == 0


# ---------------------------------------------------------------------------
# 8: authenticity batch wiring
# ---------------------------------------------------------------------------


def test_authenticity_batch_fires_only_for_flagged_review(client: TestClient) -> None:
    """Of a 2-review batch (one GENUINE, one LIKELY_FAKE), only the flagged one alerts."""
    fake_channel = FakeChannel()
    results = [
        _auth_result(label=AuthenticityLabel.GENUINE, score=0.9, text="genuine one"),
        _auth_result(label=AuthenticityLabel.LIKELY_FAKE, score=0.1, text="fake one"),
    ]

    with (
        patch("app.api.v2.authenticity.engine.score_batch", new=AsyncMock(return_value=results)),
        patch(
            "app.api.v2.authenticity.save_authenticity_audit_pg", new=MagicMock(return_value=None)
        ),
        patch("app.core.alerts.engine.is_already_alerted_pg", MagicMock(return_value=False)),
        patch("app.core.alerts.engine.get_preference_pg", MagicMock(return_value=None)),
        patch("app.core.alerts.engine.record_alert_sent_pg", MagicMock(return_value=None)),
        patch(
            "app.core.alerts.engine.get_org_notification_email_pg",
            MagicMock(return_value="seller@example.com"),
        ),
        patch("app.core.alerts.engine._get_default_channel", MagicMock(return_value=fake_channel)),
    ):
        resp = client.post(
            "/v2/authenticity/batch",
            json={"reviews": [{"text": "genuine one"}, {"text": "fake one"}]},
        )

    assert resp.status_code == 200
    assert len(fake_channel.sent) == 1
    assert fake_channel.sent[0].event.event_type == AlertEventType.LIKELY_FAKE


# ---------------------------------------------------------------------------
# 10-13: channel-send failure tolerance (e.g. Resend down) across all wired paths
# (the CSV-ingest variant of this proof lives in tests/unit/test_ingest_worker.py
# — see the module docstring above.)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extraction_survives_channel_send_failure() -> None:
    """A ChannelError from channel.send (Resend down) never breaks _run_extraction_v2.

    Proves the same failure-tolerance as test_alert_engine.py's
    test_channel_error_does_not_propagate, but through the real wired call site rather
    than at the evaluate_and_alert unit level.
    """
    from app.api.v2.extract import _run_extraction_v2
    from app.core.schemas import ReviewExtractionV2

    error_channel = ErrorChannel()
    req = ReviewRequest(text=_REVIEW_TEXT)

    with (
        patch("app.api.v2.extract.get_by_hash_pg", return_value=None),
        patch("app.api.v2.extract.save_extraction_pg", return_value=str(uuid.uuid4())),
        patch(
            "app.api.v2.extract.extract_with_llm",
            new=AsyncMock(
                return_value=(_llm_output(urgency=Urgency.high), "mock-model", 42, 150, 80, False)
            ),
        ),
        patch("app.api.v2.extract.update_usage_tokens"),
        patch("app.core.alerts.engine.is_already_alerted_pg", MagicMock(return_value=False)),
        patch("app.core.alerts.engine.get_preference_pg", MagicMock(return_value=None)),
        patch(
            "app.core.alerts.engine.record_alert_sent_pg", MagicMock(return_value=None)
        ) as mock_record,
        patch(
            "app.core.alerts.engine.get_org_notification_email_pg",
            MagicMock(return_value="seller@example.com"),
        ),
        patch("app.core.alerts.engine._get_default_channel", MagicMock(return_value=error_channel)),
    ):
        result = await _run_extraction_v2(req, _CTX)

    assert isinstance(result, ReviewExtractionV2)
    assert result.urgency == Urgency.high
    mock_record.assert_not_called()


def test_authenticity_single_survives_channel_send_failure(client: TestClient) -> None:
    """A ChannelError from channel.send never affects the /v2/authenticity response body."""
    error_channel = ErrorChannel()
    fake_result = _auth_result(label=AuthenticityLabel.LIKELY_FAKE, score=0.1, text="fake review 2")

    with (
        patch(
            "app.api.v2.authenticity.get_authenticity_audit_by_hash_pg",
            new=MagicMock(return_value=None),
        ),
        patch(
            "app.api.v2.authenticity.engine.score_single", new=AsyncMock(return_value=fake_result)
        ),
        patch(
            "app.api.v2.authenticity.save_authenticity_audit_pg", new=MagicMock(return_value=None)
        ),
        patch("app.core.alerts.engine.is_already_alerted_pg", MagicMock(return_value=False)),
        patch("app.core.alerts.engine.get_preference_pg", MagicMock(return_value=None)),
        patch(
            "app.core.alerts.engine.record_alert_sent_pg", MagicMock(return_value=None)
        ) as mock_record,
        patch(
            "app.core.alerts.engine.get_org_notification_email_pg",
            MagicMock(return_value="seller@example.com"),
        ),
        patch("app.core.alerts.engine._get_default_channel", MagicMock(return_value=error_channel)),
    ):
        resp = client.post("/v2/authenticity", json={"text": "fake review 2", "stars": 5})

    assert resp.status_code == 200
    body = resp.json()
    assert body["label"] == "likely_fake"
    assert abs(body["score"] - 0.1) < 1e-6
    mock_record.assert_not_called()


def test_authenticity_batch_survives_channel_send_failure(client: TestClient) -> None:
    """A ChannelError from channel.send never affects the /v2/authenticity/batch response body."""
    error_channel = ErrorChannel()
    results = [
        _auth_result(label=AuthenticityLabel.GENUINE, score=0.9, text="genuine two"),
        _auth_result(label=AuthenticityLabel.LIKELY_FAKE, score=0.1, text="fake two"),
    ]

    with (
        patch("app.api.v2.authenticity.engine.score_batch", new=AsyncMock(return_value=results)),
        patch(
            "app.api.v2.authenticity.save_authenticity_audit_pg", new=MagicMock(return_value=None)
        ),
        patch("app.core.alerts.engine.is_already_alerted_pg", MagicMock(return_value=False)),
        patch("app.core.alerts.engine.get_preference_pg", MagicMock(return_value=None)),
        patch(
            "app.core.alerts.engine.record_alert_sent_pg", MagicMock(return_value=None)
        ) as mock_record,
        patch(
            "app.core.alerts.engine.get_org_notification_email_pg",
            MagicMock(return_value="seller@example.com"),
        ),
        patch("app.core.alerts.engine._get_default_channel", MagicMock(return_value=error_channel)),
    ):
        resp = client.post(
            "/v2/authenticity/batch",
            json={"reviews": [{"text": "genuine two"}, {"text": "fake two"}]},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["results"]) == 2
    mock_record.assert_not_called()
