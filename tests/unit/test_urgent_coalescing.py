"""Urgent-alert coalescing: cap, window rollover, concurrency, roll-up, failure recovery.

The database is replaced by FakeAlertLog, an in-memory model of the alert_log marker-row
protocol in app/core/alerts/storage.py (claim / release / deferred / high_urgency rows) with a
real threading.Lock standing in for the per-org advisory lock. The SQL itself is NOT exercised
here (no database in unit tests) -- see the PR body for what is and is not verified.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from app.core.alerts import coalescer
from app.core.alerts.channels.base import AlertMessage, ChannelError
from app.core.alerts.channels.fake import FakeChannel
from app.core.alerts.engine import evaluate_and_alert
from app.core.alerts.rules import AlertEvent, AlertEventType
from app.core.config import get_settings
from app.core.schemas import ReviewExtraction, Urgency

ORG = "org-1"


class FakeAlertLog:
    """In-memory alert_log with the same semantics as the storage.py coalescing functions."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.now = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
        self._lock = threading.Lock()  # stands in for pg_advisory_xact_lock
        self.fail_claim = False

    def advance(self, minutes: float) -> None:
        self.now += timedelta(minutes=minutes)

    def _add(self, org_id: str, review_id: str | None, event_type: str, details: Any) -> None:
        self.rows.append(
            {
                "org_id": org_id,
                "review_id": review_id,
                "event_type": event_type,
                "details": details,
                "sent_at": self.now,
            }
        )

    def claim(self, org_id: str, token: str, window_minutes: int, max_per_window: int) -> bool:
        if self.fail_claim:
            raise RuntimeError("db down")
        with self._lock:
            cutoff = self.now - timedelta(minutes=window_minutes)
            released = {
                r["review_id"] for r in self.rows if r["event_type"] == "urgent_window_release"
            }
            active = [
                r
                for r in self.rows
                if r["org_id"] == org_id
                and r["event_type"] == "urgent_window_claim"
                and r["sent_at"] > cutoff
                and r["review_id"] not in released
            ]
            if len(active) >= max_per_window:
                return False
            self._add(org_id, token, "urgent_window_claim", {})
            return True

    def release(self, org_id: str, token: str) -> None:
        self._add(org_id, token, "urgent_window_release", {})

    def defer(self, org_id: str, review_id: str, details: dict[str, object]) -> None:
        with self._lock:
            if not any(
                r["review_id"] == review_id and r["event_type"] == "urgent_deferred"
                for r in self.rows
            ):
                self._add(org_id, review_id, "urgent_deferred", details)

    def pending(self, org_id: str) -> list[dict[str, object]]:
        sent = {r["review_id"] for r in self.rows if r["event_type"] == "high_urgency"}
        return [
            {"review_id": r["review_id"], "details": r["details"], "deferred_at": r["sent_at"]}
            for r in self.rows
            if r["org_id"] == org_id
            and r["event_type"] == "urgent_deferred"
            and r["review_id"] not in sent
        ]

    def record_sent(
        self, org_id: str, review_id: str | None, event_type: str, details: Any
    ) -> None:
        self._add(org_id, review_id, event_type, details)

    def is_alerted(self, org_id: str, review_id: str, event_type: str) -> bool:
        return any(r["review_id"] == review_id and r["event_type"] == event_type for r in self.rows)

    def count(self, event_type: str) -> int:
        return sum(1 for r in self.rows if r["event_type"] == event_type)


class FlakyChannel:
    """Fails the first `fail_times` sends with ChannelError, then records like FakeChannel."""

    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.sent: list[AlertMessage] = []

    async def send(self, message: AlertMessage) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ChannelError("resend 500")
        self.sent.append(message)


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def db() -> Iterator[FakeAlertLog]:
    log = FakeAlertLog()
    state: dict[str, Any] = {"email": "seller@example.com", "pref": None}
    log.state = state  # type: ignore[attr-defined]
    with ExitStack() as stack:
        for target, fn in {
            "claim_urgent_window_pg": log.claim,
            "release_urgent_window_pg": log.release,
            "record_urgent_deferred_pg": log.defer,
            "list_pending_urgent_deferred_pg": log.pending,
            "record_alert_sent_pg": log.record_sent,
            "get_org_notification_email_pg": lambda org_id: state["email"],
            "get_preference_pg": lambda org_id, et: state["pref"],
        }.items():
            stack.enter_context(patch(f"app.core.alerts.coalescer.{target}", fn))
        stack.enter_context(patch("app.core.alerts.engine.is_already_alerted_pg", log.is_alerted))
        stack.enter_context(patch("app.core.alerts.engine.record_alert_sent_pg", log.record_sent))
        stack.enter_context(
            patch("app.core.alerts.engine.get_preference_pg", lambda org_id, et: state["pref"])
        )
        stack.enter_context(
            patch(
                "app.core.alerts.engine.get_org_notification_email_pg",
                lambda org_id: state["email"],
            )
        )
        # Freeze the roll-up subject's "age" clock to the fake clock.
        clock = MagicMock(wraps=datetime)
        clock.now.side_effect = lambda tz=None: log.now
        stack.enter_context(patch("app.core.alerts.coalescer.datetime", clock))
        yield log


def _extraction(cons: list[str] | None = None) -> ReviewExtraction:
    return ReviewExtraction(
        product="Kettle", urgency=Urgency.high, topics=["safety"], cons=cons or ["sparks"]
    )


async def _alert(channel: Any, review_id: str, cons: list[str] | None = None) -> list[AlertEvent]:
    return await evaluate_and_alert(
        org_id=ORG, review_id=review_id, extraction=_extraction(cons), channel=channel
    )


# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_event_sends_immediately_second_inside_window_is_deferred(
    db: FakeAlertLog,
) -> None:
    ch = FakeChannel()
    first = await _alert(ch, "r1")
    second = await _alert(ch, "r2")

    assert len(first) == 1 and second == []
    assert len(ch.sent) == 1
    assert db.is_alerted(ORG, "r1", "high_urgency")
    # Deferred, not dropped, and not marked as sent (so the digest/roll-up can still find it).
    assert not db.is_alerted(ORG, "r2", "high_urgency")
    assert [p["review_id"] for p in db.pending(ORG)] == ["r2"]


@pytest.mark.asyncio
async def test_window_rollover_next_event_carries_the_rollup(db: FakeAlertLog) -> None:
    ch = FakeChannel()
    await _alert(ch, "r1")
    await _alert(ch, "r2", cons=["smoke"])
    await _alert(ch, "r3", cons=["fire", "burns hand"])
    assert len(ch.sent) == 1

    db.advance(15.5)  # window closed
    await _alert(ch, "r4")

    assert len(ch.sent) == 2, "the new window's email carries the roll-up, not a third email"
    body = ch.sent[1].body_text
    assert "2 more urgent reviews" in body
    assert "r3" in body and "r2" in body
    assert "https://app.samidhareviews.xyz" in body
    assert "(+2 more)" in ch.sent[1].subject
    # Folded events are retired so they are never sent twice.
    assert db.pending(ORG) == []
    assert db.is_alerted(ORG, "r2", "high_urgency") and db.is_alerted(ORG, "r3", "high_urgency")


@pytest.mark.asyncio
async def test_cap_is_env_overridable(db: FakeAlertLog, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("URGENT_ALERT_MAX_PER_WINDOW", "3")
    monkeypatch.setenv("URGENT_ALERT_WINDOW_MINUTES", "5")
    get_settings.cache_clear()
    ch = FakeChannel()
    for i in range(5):
        await _alert(ch, f"r{i}")
    assert len(ch.sent) == 3
    db.advance(5.1)  # custom 5-minute window has closed (15 would not have)
    await _alert(ch, "r9")
    assert len(ch.sent) == 4


@pytest.mark.asyncio
async def test_two_events_at_the_same_instant_send_exactly_one_email(db: FakeAlertLog) -> None:
    """Concurrent ingest workers: the DB-side claim (lock stand-in) lets exactly one win."""
    ch = FakeChannel()
    results = await asyncio.gather(*(_alert(ch, f"r{i}") for i in range(2)))
    assert len(ch.sent) == 1
    assert sorted(len(r) for r in results) == [0, 1]
    assert db.count("urgent_window_claim") == 1
    assert db.count("urgent_deferred") == 1


@pytest.mark.asyncio
async def test_failed_send_releases_slot_and_keeps_event_for_rollup(db: FakeAlertLog) -> None:
    ch = FlakyChannel(fail_times=1)
    first = await _alert(ch, "r1")
    assert first == [] and ch.sent == []
    assert not db.is_alerted(ORG, "r1", "high_urgency")
    assert [p["review_id"] for p in db.pending(ORG)] == ["r1"], "failed event must stay eligible"

    # Slot was released, so the very next event is NOT throttled by the failed attempt.
    second = await _alert(ch, "r2")
    assert len(second) == 1 and len(ch.sent) == 1
    # r1 rode along in r2's email and is retired.
    assert "1 more urgent review" in ch.sent[0].body_text
    assert db.pending(ORG) == []


@pytest.mark.asyncio
async def test_failed_rollup_send_leaves_events_pending(db: FakeAlertLog) -> None:
    ok = FakeChannel()
    await _alert(ok, "r1")
    await _alert(ok, "r2")
    db.advance(20)

    bad = FlakyChannel(fail_times=1)
    assert await coalescer.flush_deferred_urgent_for_org(ORG, bad) == []
    assert [p["review_id"] for p in db.pending(ORG)] == ["r2"]
    # Slot released -> an immediate retry is allowed and succeeds.
    assert await coalescer.flush_deferred_urgent_for_org(ORG, bad) == ["r2"]
    assert db.pending(ORG) == []


@pytest.mark.asyncio
async def test_unsubscribed_org_sends_defers_and_claims_nothing(db: FakeAlertLog) -> None:
    db.state["email"] = None  # type: ignore[attr-defined]
    ch = FakeChannel()
    for i in range(3):
        await _alert(ch, f"r{i}")
    assert ch.sent == []
    assert db.rows == [], "unsubscribed org: no slot consumed, nothing deferred"
    assert await coalescer.flush_deferred_urgent_for_org(ORG, ch) == []


@pytest.mark.asyncio
async def test_disabled_preference_untouched_by_coalescing(db: FakeAlertLog) -> None:
    db.state["pref"] = {"enabled": False, "frequency": "immediate"}  # type: ignore[attr-defined]
    ch = FakeChannel()
    await _alert(ch, "r1")
    assert ch.sent == [] and db.rows == []


@pytest.mark.asyncio
async def test_daily_digest_preference_untouched_by_coalescing(db: FakeAlertLog) -> None:
    db.state["pref"] = {"enabled": True, "frequency": "daily_digest"}  # type: ignore[attr-defined]
    ch = FakeChannel()
    await _alert(ch, "r1")
    assert ch.sent == [] and db.rows == []


@pytest.mark.asyncio
async def test_other_event_types_are_not_coalesced(db: FakeAlertLog) -> None:
    ch = FakeChannel()
    for i in range(3):
        await evaluate_and_alert(
            org_id=ORG,
            review_id=f"c{i}",
            precomputed_events=[AlertEvent(AlertEventType.FAKE_CLUSTER, {"count": 3})],
            channel=ch,
        )
    assert len(ch.sent) == 3
    assert db.count("urgent_window_claim") == 0


@pytest.mark.asyncio
async def test_coalescer_db_error_fails_open_and_still_sends(db: FakeAlertLog) -> None:
    db.fail_claim = True
    ch = FakeChannel()
    out = await _alert(ch, "r1")
    assert len(out) == 1 and len(ch.sent) == 1


@pytest.mark.asyncio
async def test_flush_sends_one_rollup_with_top_few_and_respects_window(
    db: FakeAlertLog,
) -> None:
    ch = FakeChannel()
    await _alert(ch, "r0")
    for i in range(1, 9):
        await _alert(ch, f"r{i}", cons=["x"] * i)  # r8 has the most issues

    # Window still busy -> flush is a no-op.
    assert await coalescer.flush_deferred_urgent_for_org(ORG, ch) == []
    db.advance(16)
    sent = await coalescer.flush_deferred_urgent_for_org(ORG, ch)

    assert len(sent) == 8 and len(ch.sent) == 2
    msg = ch.sent[1]
    assert msg.subject == "8 more urgent reviews since your last urgent alert"
    lines = msg.body_text.splitlines()
    listed = [ln for ln in lines if ln.startswith("- review")]
    assert len(listed) == coalescer.ROLLUP_TOP_N
    assert listed[0].startswith("- review r8:")  # most issues first
    assert "...and 3 more" in msg.body_text
    assert "https://app.samidhareviews.xyz" in msg.body_text
    # Idempotent: a second flush has nothing left.
    db.advance(16)
    assert await coalescer.flush_deferred_urgent_for_org(ORG, ch) == []


@pytest.mark.asyncio
async def test_rollup_subject_uses_window_wording_when_fresh(db: FakeAlertLog) -> None:
    ch = FakeChannel()
    await _alert(ch, "r0")
    await _alert(ch, "r1")
    await _alert(ch, "r2")
    msg = coalescer.build_urgent_rollup_email(ORG, "s@example.com", db.pending(ORG))  # type: ignore[arg-type]
    assert msg.subject == "2 more urgent reviews in the last 15 minutes"


@pytest.mark.asyncio
async def test_200_event_csv_burst_sends_at_most_two_emails(db: FakeAlertLog) -> None:
    ch = FakeChannel()
    # 200 reviews ingested over ~2 minutes by overlapping workers.
    for batch in range(20):
        await asyncio.gather(*(_alert(ch, f"r{batch * 10 + j}") for j in range(10)))
        db.advance(0.1)
    assert len(ch.sent) == 1, "burst inside one window: only the first event is emailed"
    assert len(db.pending(ORG)) == 199

    db.advance(15)  # window closes; the digest/roll-up sweep delivers everything held back
    assert len(await coalescer.flush_deferred_urgent_for_org(ORG, ch)) == 199
    assert len(ch.sent) == 2
    assert "199 more urgent reviews" in ch.sent[1].body_text
    assert db.pending(ORG) == []
