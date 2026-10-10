"""Unit tests for the weekly digest cadence (S20 M3b).

Covers: stats aggregation, weekly body (text + HTML escaping, dashboard link, unsubscribe),
the weekly collect/send/record flow (idempotent re-run, no events -> no send, failed send
records nothing, stats failure degrades), the cadence selector on POST /internal/digest/run,
the preference API accepting weekly_digest, the immediate engine deferring it, and the Resend
channel sending the HTML part. Hermetic: every storage call and setting is patched.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from app.auth.api_key import ApiKeyContext
from app.auth.session import require_session
from app.core.alerts.channels.base import AlertMessage, ChannelError
from app.core.alerts.channels.fake import FakeChannel
from app.core.alerts.channels.resend_channel import ResendChannel
from app.core.alerts.digest import (
    PendingDigestEvent,
    build_weekly_digest_email,
    collect_pending_for_org,
    compute_weekly_stats,
    run_digest_for_org,
)
from app.core.alerts.rules import AlertEvent, AlertEventType
from fastapi.testclient import TestClient

_NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
_ORG_CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)
_WEEKLY_PREF = {"event_type": "x", "enabled": True, "frequency": "weekly_digest"}
_DAILY_PREF = {"event_type": "x", "enabled": True, "frequency": "daily_digest"}


def _row(
    days_ago: float,
    urgency: str = "low",
    topics: list[str] | None = None,
    cons: list[str] | None = None,
) -> dict[str, object]:
    return {
        "urgency": urgency,
        "topics": topics if topics is not None else [],
        "cons": cons if cons is not None else [],
        "created_at": _NOW - timedelta(days=days_ago),
    }


def _extraction_row(input_hash: str = "h1") -> dict[str, object]:
    return {
        "input_hash": input_hash,
        "product": "P",
        "urgency": "high",
        "topics": ["battery"],
        "cons": ["drains fast"],
        "created_at": datetime(2026, 1, 2, tzinfo=UTC),
    }


def _pending(review_id: str = "r1", cons: list[str] | None = None) -> PendingDigestEvent:
    return PendingDigestEvent(
        review_id=review_id,
        event=AlertEvent(
            event_type=AlertEventType.HIGH_URGENCY,
            details={"topics": ["battery"], "cons": cons if cons is not None else ["bad"]},
        ),
    )


def _settings(dashboard_url: str = "https://dash.example.test/") -> MagicMock:
    s = MagicMock()
    s.dashboard_url = dashboard_url
    return s


# ---------------------------------------------------------------------------
# compute_weekly_stats
# ---------------------------------------------------------------------------


def test_stats_week_over_week_counts_and_window_edges() -> None:
    rows = [
        _row(1, urgency="high", topics=["Battery"], cons=["x"]),
        _row(3, topics=["battery "], cons=["y"]),
        _row(7.0),  # exactly 7 days ago: belongs to the PREVIOUS window (this is (now-7d, now])
        _row(10, urgency="high", topics=["battery"], cons=["z"]),
        _row(14.0),  # exactly 14 days ago: outside both windows
        _row(30),  # outside both
    ]
    stats = compute_weekly_stats(rows, _NOW)
    assert (stats.reviews_this, stats.reviews_prev) == (2, 2)
    assert (stats.urgent_this, stats.urgent_prev) == (1, 1)
    # topics are normalised (case, whitespace) and compared across weeks
    assert stats.top_topics == (("battery", 2, 1),)


def test_stats_empty_and_hostile_rows_do_not_crash() -> None:
    assert compute_weekly_stats([], _NOW).reviews_this == 0
    hostile: list[dict[str, object]] = [
        {"urgency": None, "topics": None, "cons": None, "created_at": None},
        {"urgency": "high", "topics": "not-a-list", "cons": "nope", "created_at": _NOW},
        {"urgency": "low", "topics": [None, 5, "", "  ", "ok"], "cons": ["c"], "created_at": _NOW},
        {"created_at": _NOW.replace(tzinfo=None)},  # naive datetime is treated as UTC
    ]
    stats = compute_weekly_stats(hostile, _NOW)
    assert stats.reviews_this == 3  # the created_at=None row is ignored
    assert stats.top_topics == (("ok", 1, 0),)


def test_stats_top_topics_capped_and_only_complaints() -> None:
    rows = [_row(1, topics=[f"t{i}"], cons=["c"]) for i in range(9)]
    rows.append(_row(1, topics=["praise-only"], cons=[]))  # not a complaint
    stats = compute_weekly_stats(rows, _NOW)
    assert len(stats.top_topics) == 5
    assert "praise-only" not in {t for t, _, _ in stats.top_topics}


# ---------------------------------------------------------------------------
# build_weekly_digest_email
# ---------------------------------------------------------------------------


def _build(
    events: list[PendingDigestEvent], dashboard_url: str = "https://dash.example.test/"
) -> AlertMessage | None:
    stats = compute_weekly_stats(
        [_row(1, urgency="high", topics=["battery"], cons=["x"]), _row(9)], _NOW
    )
    with (
        patch("app.core.alerts.digest.get_settings", return_value=_settings(dashboard_url)),
        patch(
            "app.core.alerts.digest.build_unsubscribe_url",
            return_value="https://api.example.test/unsubscribe?org=o&token=t",
        ),
    ):
        return build_weekly_digest_email("org1", "s@example.com", events, stats)


def test_weekly_email_none_when_no_events() -> None:
    assert _build([]) is None


def test_weekly_email_text_and_html_content() -> None:
    msg = _build([_pending("r1"), _pending("r2")])
    assert msg is not None
    assert msg.subject == "Samidha Reviews weekly digest: 2 event(s) need attention"
    assert "Reviews analysed: 1 (+0 vs 1 the week before)" in msg.body_text
    assert "battery: 1 (+1 vs 0 the week before)" in msg.body_text
    assert "Open your dashboard: https://dash.example.test/dashboard" in msg.body_text
    assert "unsubscribe?org=o&token=t" in msg.body_text
    assert msg.unsubscribe_url == "https://api.example.test/unsubscribe?org=o&token=t"
    assert msg.body_html is not None
    assert 'href="https://dash.example.test/dashboard"' in msg.body_html
    assert "Stop receiving these emails" in msg.body_html


def test_weekly_email_html_escapes_hostile_review_content() -> None:
    msg = _build([_pending("r1", cons=["<script>alert(1)</script>"])])
    assert msg is not None and msg.body_html is not None
    assert "<script>" not in msg.body_html
    assert "&lt;script&gt;" in msg.body_html


def test_weekly_email_omits_dashboard_link_when_unconfigured() -> None:
    msg = _build([_pending()], dashboard_url="")
    assert msg is not None and msg.body_html is not None
    assert "Open your dashboard" not in msg.body_text
    assert '<a href="https://dash' not in msg.body_html


def test_weekly_email_caps_listed_events() -> None:
    msg = _build([_pending(f"r{i}") for i in range(25)])
    assert msg is not None
    assert msg.body_text.count("review r") == 10
    assert "...and 15 more" in msg.body_text


def test_weekly_email_without_stats_still_builds() -> None:
    with (
        patch("app.core.alerts.digest.get_settings", return_value=_settings()),
        patch("app.core.alerts.digest.build_unsubscribe_url", return_value=None),
    ):
        msg = build_weekly_digest_email("org1", "s@example.com", [_pending()], None)
    assert msg is not None
    assert "This week vs" not in msg.body_text
    assert "Stop receiving" not in msg.body_text  # no unsubscribe configured -> no footer link
    assert msg.unsubscribe_url is None


# ---------------------------------------------------------------------------
# collect_pending_for_org / run_digest_for_org with cadence="weekly"
# ---------------------------------------------------------------------------


def _flow_patches(
    pref: dict[str, object] = _WEEKLY_PREF,
    extraction_rows: list[dict[str, object]] | None = None,
    already: bool = False,
    email: str | None = "seller@example.com",
) -> tuple[list[object], dict[str, MagicMock]]:
    mocks = {
        "watermark": MagicMock(return_value=_ORG_CREATED_AT),
        "record": MagicMock(return_value=None),
        "extractions": MagicMock(return_value=extraction_rows or []),
    }
    patches = [
        patch("app.core.alerts.digest.get_preference_pg", MagicMock(return_value=pref)),
        patch("app.core.alerts.digest.get_last_digest_watermark_pg", mocks["watermark"]),
        patch(
            "app.core.alerts.digest.get_org_created_at_pg", MagicMock(return_value=_ORG_CREATED_AT)
        ),
        patch("app.core.alerts.digest.list_extractions_since_pg", mocks["extractions"]),
        patch(
            "app.core.alerts.digest.list_authenticity_audits_since_pg", MagicMock(return_value=[])
        ),
        patch("app.core.alerts.digest.is_already_alerted_pg", MagicMock(return_value=already)),
        patch(
            "app.core.alerts.digest.get_org_notification_email_pg", MagicMock(return_value=email)
        ),
        patch("app.core.alerts.digest.record_alert_sent_pg", mocks["record"]),
        patch(
            "app.core.alerts.digest.list_extraction_summaries_since_pg",
            MagicMock(return_value=[_row(1, urgency="high", topics=["battery"], cons=["x"])]),
        ),
        patch("app.core.alerts.digest.get_settings", return_value=_settings()),
        patch("app.core.alerts.digest.build_unsubscribe_url", return_value=None),
    ]
    return patches, mocks


async def _run(
    channel: object,
    cadence: str = "weekly",
    **kwargs: object,
) -> tuple[list[PendingDigestEvent], dict[str, MagicMock]]:
    patches, mocks = _flow_patches(**kwargs)  # type: ignore[arg-type]
    from contextlib import ExitStack

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)  # type: ignore[arg-type]
        result = await run_digest_for_org("org1", channel, cadence)  # type: ignore[arg-type]
    return result, mocks


@pytest.mark.asyncio
async def test_weekly_collect_ignores_daily_and_immediate_prefs() -> None:
    for pref in (_DAILY_PREF, {"event_type": "x", "enabled": True, "frequency": "immediate"}):
        patches, _ = _flow_patches(pref=pref, extraction_rows=[_extraction_row()])
        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)  # type: ignore[arg-type]
            assert await collect_pending_for_org("org1", "weekly") == []


@pytest.mark.asyncio
async def test_daily_collect_ignores_weekly_pref() -> None:
    patches, _ = _flow_patches(pref=_WEEKLY_PREF, extraction_rows=[_extraction_row()])
    from contextlib import ExitStack

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)  # type: ignore[arg-type]
        assert await collect_pending_for_org("org1") == []


@pytest.mark.asyncio
async def test_weekly_send_records_with_cadence_tag_and_weekly_watermark() -> None:
    fake = FakeChannel()
    result, mocks = await _run(fake, extraction_rows=[_extraction_row("h1"), _extraction_row("h2")])
    assert len(result) == 2
    assert len(fake.sent) == 1
    assert fake.sent[0].body_html is not None
    assert mocks["record"].call_count == 2
    for call in mocks["record"].call_args_list:
        assert call.args[3]["cadence"] == "weekly"
    # the watermark was read for the weekly cadence only
    assert all(c.args[2] == "weekly" for c in mocks["watermark"].call_args_list)


@pytest.mark.asyncio
async def test_weekly_no_events_sends_nothing_and_records_nothing() -> None:
    fake = FakeChannel()
    result, mocks = await _run(fake, extraction_rows=[])
    assert result == []
    assert fake.sent == []
    mocks["record"].assert_not_called()


@pytest.mark.asyncio
async def test_weekly_rerun_is_idempotent_once_events_are_logged() -> None:
    """Second run: every event is already in alert_log, so nothing is sent or recorded."""
    fake = FakeChannel()
    result, mocks = await _run(fake, extraction_rows=[_extraction_row()], already=True)
    assert result == []
    assert fake.sent == []
    mocks["record"].assert_not_called()


@pytest.mark.asyncio
async def test_weekly_unsubscribed_org_sends_nothing_records_nothing() -> None:
    """Unsubscribe clears notification_email -> no recipient -> events stay re-collectable."""
    fake = FakeChannel()
    result, mocks = await _run(fake, extraction_rows=[_extraction_row()], email=None)
    assert result == []
    assert fake.sent == []
    mocks["record"].assert_not_called()


class _ErrorChannel:
    async def send(self, message: object) -> None:
        raise ChannelError("delivery failed")


@pytest.mark.asyncio
async def test_weekly_failed_send_records_nothing() -> None:
    result, mocks = await _run(_ErrorChannel(), extraction_rows=[_extraction_row()])
    assert result == []
    mocks["record"].assert_not_called()


@pytest.mark.asyncio
async def test_weekly_stats_failure_still_sends_without_comparison() -> None:
    fake = FakeChannel()
    patches, mocks = _flow_patches(extraction_rows=[_extraction_row()])
    from contextlib import ExitStack

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)  # type: ignore[arg-type]
        stack.enter_context(
            patch(
                "app.core.alerts.digest.list_extraction_summaries_since_pg",
                MagicMock(side_effect=RuntimeError("db down")),
            )
        )
        result = await run_digest_for_org("org1", fake, "weekly")
    assert len(result) == 1
    assert len(fake.sent) == 1
    assert "This week vs" not in fake.sent[0].body_text
    assert mocks["record"].call_count == 1


@pytest.mark.asyncio
async def test_daily_run_unchanged_by_cadence_support() -> None:
    """Daily rows are not tagged and the daily watermark call keeps its 2-arg shape."""
    fake = FakeChannel()
    result, mocks = await _run(
        fake, cadence="daily", pref=_DAILY_PREF, extraction_rows=[_extraction_row()]
    )
    assert len(result) == 1
    assert fake.sent[0].body_html is None
    assert "cadence" not in mocks["record"].call_args.args[3]
    assert all(len(c.args) == 2 for c in mocks["watermark"].call_args_list)


# ---------------------------------------------------------------------------
# POST /internal/digest/run?cadence=
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from app.main import app

    yield TestClient(app, raise_server_exceptions=False)


def _trigger_settings() -> MagicMock:
    s = MagicMock()
    s.digest_trigger_token = "real_secret"
    return s


def _post(
    client: TestClient, path: str, org_lister: str, orgs: list[str], run: AsyncMock
) -> object:
    with (
        patch("app.api.internal.digest.get_settings", return_value=_trigger_settings()),
        patch(f"app.api.internal.digest.{org_lister}", return_value=orgs),
        patch("app.api.internal.digest.run_digest_for_org", new=run),
        patch("app.api.internal.digest._get_default_channel", return_value=MagicMock()),
    ):
        return client.post(path, headers={"X-Digest-Trigger-Token": "real_secret"})


def test_weekly_cadence_uses_weekly_org_list_and_passes_cadence(client: TestClient) -> None:
    run = AsyncMock(return_value=[_pending()])
    resp = _post(
        client,
        "/internal/digest/run?cadence=weekly",
        "list_orgs_with_weekly_digest_pg",
        ["org-a"],
        run,
    )
    assert resp.status_code == 200  # type: ignore[attr-defined]
    body = resp.json()  # type: ignore[attr-defined]
    assert body["cadence"] == "weekly"
    assert body["sent_per_org"] == {"org-a": 1}
    assert run.call_args.args[2] == "weekly"


def test_default_cadence_is_daily_and_call_shape_unchanged(client: TestClient) -> None:
    run = AsyncMock(return_value=[])
    resp = _post(client, "/internal/digest/run", "list_orgs_with_daily_digest_pg", ["org-a"], run)
    assert resp.status_code == 200  # type: ignore[attr-defined]
    assert resp.json()["cadence"] == "daily"  # type: ignore[attr-defined]
    assert len(run.call_args.args) == 2


def test_weekly_one_org_failing_does_not_stop_others(client: TestClient) -> None:
    async def _run_org(org_id: str, channel: object, cadence: str) -> list[PendingDigestEvent]:
        if org_id == "org-a":
            raise RuntimeError("boom")
        return [_pending()]

    resp = _post(
        client,
        "/internal/digest/run?cadence=weekly",
        "list_orgs_with_weekly_digest_pg",
        ["org-a", "org-b"],
        AsyncMock(side_effect=_run_org),
    )
    body = resp.json()  # type: ignore[attr-defined]
    assert resp.status_code == 200  # type: ignore[attr-defined]
    assert body["failed_orgs"] == ["org-a"]
    assert body["sent_per_org"] == {"org-b": 1}


@pytest.mark.parametrize("bad", ["monthly", "", "WEEKLY", "weekly;drop"])
def test_invalid_cadence_is_422_after_auth(client: TestClient, bad: str) -> None:
    with patch("app.api.internal.digest.get_settings", return_value=_trigger_settings()):
        resp = client.post(
            f"/internal/digest/run?cadence={bad}",
            headers={"X-Digest-Trigger-Token": "real_secret"},
        )
    assert resp.status_code == 422


def test_invalid_cadence_without_token_is_401_not_422(client: TestClient) -> None:
    with patch("app.api.internal.digest.get_settings", return_value=_trigger_settings()):
        resp = client.post("/internal/digest/run?cadence=monthly")
    assert resp.status_code == 401


def test_weekly_cadence_requires_token(client: TestClient) -> None:
    with patch("app.api.internal.digest.get_settings", return_value=_trigger_settings()):
        resp = client.post("/internal/digest/run?cadence=weekly")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Preference API, immediate engine, Resend channel
# ---------------------------------------------------------------------------

_CTX = ApiKeyContext(
    org_id=str(uuid.uuid4()), api_key_id=str(uuid.uuid4()), key_name="k", usage_record_id=""
)


@pytest.fixture()
async def api() -> httpx.AsyncClient:
    from app.main import app

    app.dependency_overrides[require_session] = lambda: _CTX
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.parametrize("event_type", ["high_urgency", "likely_fake"])
async def test_weekly_digest_accepted_for_digestible_types(
    api: httpx.AsyncClient, event_type: str
) -> None:
    with patch("app.api.bff.alerts.upsert_preference_pg", return_value=None) as up:
        resp = await api.put(
            f"/bff/alerts/preferences/{event_type}",
            json={"enabled": True, "frequency": "weekly_digest"},
        )
    assert resp.status_code == 200
    assert up.call_args.args[3] == "weekly_digest"


@pytest.mark.parametrize(
    "event_type", ["batch_defect", "fake_campaign", "fake_cluster", "topic_spike"]
)
async def test_weekly_digest_rejected_for_non_digestible_types(
    api: httpx.AsyncClient, event_type: str
) -> None:
    resp = await api.put(
        f"/bff/alerts/preferences/{event_type}",
        json={"enabled": True, "frequency": "weekly_digest"},
    )
    assert resp.status_code == 422
    assert "weekly_digest" in resp.json()["detail"]


async def test_unknown_frequency_still_rejected(api: httpx.AsyncClient) -> None:
    resp = await api.put(
        "/bff/alerts/preferences/high_urgency", json={"enabled": True, "frequency": "monthly"}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_immediate_engine_defers_weekly_digest_pref() -> None:
    from app.core.alerts.engine import evaluate_and_alert
    from app.core.schemas import ReviewExtraction, Urgency

    fake = FakeChannel()
    with (
        patch("app.core.alerts.engine.is_already_alerted_pg", MagicMock(return_value=False)),
        patch(
            "app.core.alerts.engine.get_preference_pg",
            MagicMock(
                return_value={
                    "event_type": "high_urgency",
                    "enabled": True,
                    "frequency": "weekly_digest",
                }
            ),
        ),
        patch(
            "app.core.alerts.engine.record_alert_sent_pg", MagicMock(return_value=None)
        ) as mock_record,
    ):
        await evaluate_and_alert(
            org_id="org1",
            review_id="rev1",
            extraction=ReviewExtraction(
                product="P", urgency=Urgency.high, topics=["t"], cons=["c"]
            ),
            auth=None,
            channel=fake,
            recipient_email="seller@example.com",
        )
    assert fake.sent == []
    mock_record.assert_not_called()


@pytest.mark.asyncio
async def test_resend_sends_html_part_only_when_present() -> None:
    s = MagicMock()
    s.resend_api_key = "re_test_key"
    s.resend_from_email = "onboarding@resend.dev"
    s.resend_from_name = ""
    s.resend_reply_to = ""
    with patch("app.core.alerts.channels.resend_channel.get_settings", return_value=s):
        channel = ResendChannel()
    event = AlertEvent(event_type=AlertEventType.HIGH_URGENCY, details={})
    resp = MagicMock()
    resp.__getitem__.return_value = "msg-1"
    for html_body in ("<p>hi</p>", None):
        msg = AlertMessage(
            org_id="o",
            event=event,
            subject="s",
            body_text="t",
            recipient_email="a@b.c",
            unsubscribe_url="https://x.test/u",
            body_html=html_body,
        )
        with patch(
            "app.core.alerts.channels.resend_channel.resend.Emails.send_async",
            new=AsyncMock(return_value=resp),
        ) as send:
            await channel.send(msg)
        params = send.call_args[0][0]
        assert params["text"] == "t"
        assert params["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        if html_body:
            assert params["html"] == "<p>hi</p>"
        else:
            assert "html" not in params
