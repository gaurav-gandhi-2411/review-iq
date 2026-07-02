"""End-to-end alert flow: high-urgency review → ResendChannel → real Resend email.

Run explicitly (never in the default CI suite — burns Resend quota):
    uv run pytest tests/integration/test_resend_e2e.py -v -m integration -s

What this test proves:
  1. A high-urgency review (harm signal: "ears bleed") triggers evaluate_and_alert.
  2. ResendChannel delivers a real email via the Resend sandbox API.
  3. Exactly 1 alert_log row is written (resend API response captured).
  4. A re-run with the same review_id sends 0 emails (dedupe gate holds).
  5. alert_log still has exactly 1 row after the re-run.

Requires: RESEND_API_KEY, RESEND_FROM_EMAIL, RESEND_TEST_RECIPIENT, SUPABASE_DIRECT_URL,
          SUPABASE_DATABASE_URL in .env.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg2
import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env")

from datetime import UTC, datetime  # noqa: E402 — must come after load_dotenv

from app.core.alerts.channels.resend_channel import ResendChannel  # noqa: E402
from app.core.alerts.engine import evaluate_and_alert  # noqa: E402
from app.core.alerts.rules import AlertEventType  # noqa: E402
from app.core.authenticity.schema import AuthenticityLabel, AuthenticityResult  # noqa: E402
from app.core.schemas import ReviewExtraction, Urgency  # noqa: E402


# ---------------------------------------------------------------------------
# DB helpers (direct connection — bypasses pooler for setup/teardown)
# ---------------------------------------------------------------------------


def _direct_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(os.environ["SUPABASE_DIRECT_URL"])


def _count_alert_log(org_id: str, review_id: str, event_type: str) -> int:
    """Count alert_log rows for this org+review+event (service_role, no RLS)."""
    conn = _direct_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM public.alert_log "
            "WHERE org_id = %s AND review_id = %s AND event_type = %s",
            (org_id, review_id, event_type),
        )
        return int(cur.fetchone()[0])  # type: ignore[index]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def test_org_id() -> str:  # type: ignore[return]
    """Create a throw-away org; delete it (cascades alert_log) after the module."""
    org_id = str(uuid.uuid4())
    conn = _direct_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO public.organizations (id, name, slug) VALUES (%s, %s, %s)",
            (org_id, "ResendE2ETestOrg", f"resend-e2e-{org_id[:8]}"),
        )
        conn.commit()
    finally:
        conn.close()

    yield org_id  # type: ignore[misc]

    conn = _direct_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM public.organizations WHERE id = %s", (org_id,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Object factories
# ---------------------------------------------------------------------------


def _harm_signal_extraction() -> ReviewExtraction:
    """High-urgency extraction matching the 'ears bleed' row in synthetic_reviews_test.csv."""
    return ReviewExtraction(
        product="Generic OEM Gaming Headset",
        stars=1,
        urgency=Urgency.high,
        topics=["ear comfort", "physical harm", "return"],
        cons=["ear cups pressed too hard", "ears bleeding after 2h", "terrible design"],
        sentiment=None,
    )


def _genuine_auth() -> AuthenticityResult:
    """Genuine-scored auth result (harm signal is real, not fake)."""
    return AuthenticityResult(
        score=0.82,
        label=AuthenticityLabel.GENUINE,
        flags=[],
        reasons="specific harm detail and return intent; genuine distress signal",
        review_hash="e2e_test_harm_signal_hash",
        scored_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestResendChannelE2E:
    def test_real_email_sent_and_logged(self, test_org_id: str) -> None:
        """First run: evaluate → ResendChannel sends → alert_log written."""
        import asyncio

        review_id = str(uuid.uuid4())
        recipient = os.environ["RESEND_TEST_RECIPIENT"]

        channel = ResendChannel()
        result = asyncio.run(
            evaluate_and_alert(
                org_id=test_org_id,
                review_id=review_id,
                extraction=_harm_signal_extraction(),
                auth=_genuine_auth(),
                channel=channel,
                recipient_email=recipient,
            )
        )

        # 1. Engine returned the high-urgency event.
        assert len(result) == 1, f"Expected 1 alert event, got {result}"
        assert result[0].event_type == AlertEventType.HIGH_URGENCY

        # 2. Resend API responded with a message ID.
        assert channel.last_response is not None, "ResendChannel.last_response must be set"
        assert channel.last_response.id, (
            f"Resend API must return a non-empty message id; got: {channel.last_response}"
        )
        print(f"\n=== Resend API response ===\nmessage_id: {channel.last_response.id}\n")

        # 3. alert_log has exactly 1 row for this review+event.
        log_count = _count_alert_log(test_org_id, review_id, "high_urgency")
        assert log_count == 1, f"Expected 1 alert_log row, found {log_count}"

        # Store review_id for the dedupe test (shared via class attribute).
        TestResendChannelE2E._review_id = review_id

    def test_dedupe_second_run_sends_zero(self, test_org_id: str) -> None:
        """Re-run with the same review_id: engine dedupes, 0 emails sent."""
        import asyncio

        review_id = TestResendChannelE2E._review_id  # type: ignore[attr-defined]
        recipient = os.environ["RESEND_TEST_RECIPIENT"]

        channel = ResendChannel()
        result = asyncio.run(
            evaluate_and_alert(
                org_id=test_org_id,
                review_id=review_id,
                extraction=_harm_signal_extraction(),
                auth=_genuine_auth(),
                channel=channel,
                recipient_email=recipient,
            )
        )

        assert result == [], f"Dedupe must suppress second send; got events: {result}"
        assert channel.last_response is None, "No Resend call should have been made"

        # alert_log must still have exactly 1 row — not 2.
        log_count = _count_alert_log(test_org_id, review_id, "high_urgency")
        assert log_count == 1, (
            f"alert_log must have exactly 1 row after dedupe re-run, found {log_count}"
        )
        print("\n=== Dedupe gate held: 0 emails sent, alert_log count unchanged (1) ===\n")
