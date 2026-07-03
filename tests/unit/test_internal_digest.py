"""Unit tests for POST /internal/digest/run — token-protected digest sweep trigger."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.core.alerts.digest import PendingDigestEvent
from app.core.alerts.rules import AlertEvent, AlertEventType
from fastapi.testclient import TestClient


def _make_mock_settings(trigger_token: str = "") -> MagicMock:
    """Mimic the MagicMock-settings pattern used by test_google_connector.py."""
    s = MagicMock()
    s.digest_trigger_token = trigger_token
    return s


@pytest.fixture
def client() -> TestClient:
    """TestClient against the real app — no auth override, header token is the protection."""
    from app.main import app

    yield TestClient(app, raise_server_exceptions=False)


def _fake_pending_event(review_id: str) -> PendingDigestEvent:
    """Construct a minimal PendingDigestEvent for test fixtures."""
    return PendingDigestEvent(
        review_id=review_id,
        event=AlertEvent(
            event_type=AlertEventType.HIGH_URGENCY,
            details={"topics": ["battery"], "cons": ["drains fast"]},
        ),
    )


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------


def test_missing_token_returns_401(client: TestClient) -> None:
    """Configured token, no header sent -> 401."""
    with patch(
        "app.api.internal.digest.get_settings",
        return_value=_make_mock_settings(trigger_token="real_secret"),
    ):
        resp = client.post("/internal/digest/run")
    assert resp.status_code == 401


def test_wrong_token_returns_401(client: TestClient) -> None:
    """Configured token, wrong header value -> 401."""
    with patch(
        "app.api.internal.digest.get_settings",
        return_value=_make_mock_settings(trigger_token="real_secret"),
    ):
        resp = client.post(
            "/internal/digest/run",
            headers={"X-Digest-Trigger-Token": "wrong_token"},
        )
    assert resp.status_code == 401


def test_unconfigured_server_returns_503(client: TestClient) -> None:
    """digest_trigger_token empty/unset -> 503, even with some header value provided."""
    with patch(
        "app.api.internal.digest.get_settings",
        return_value=_make_mock_settings(trigger_token=""),
    ):
        resp = client.post(
            "/internal/digest/run",
            headers={"X-Digest-Trigger-Token": "anything"},
        )
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Sweep behavior
# ---------------------------------------------------------------------------


def test_correct_token_runs_sweep(client: TestClient) -> None:
    """Correct token; sweeps two orgs, aggregates counts correctly."""
    org_b_events = [_fake_pending_event("rev-1"), _fake_pending_event("rev-2")]

    async def _fake_run_digest_for_org(org_id: str, channel: object) -> list[PendingDigestEvent]:
        if org_id == "org-a":
            return []
        return org_b_events

    with (
        patch(
            "app.api.internal.digest.get_settings",
            return_value=_make_mock_settings(trigger_token="real_secret"),
        ),
        patch(
            "app.api.internal.digest.list_orgs_with_daily_digest_pg",
            return_value=["org-a", "org-b"],
        ),
        patch(
            "app.api.internal.digest.run_digest_for_org",
            new=AsyncMock(side_effect=_fake_run_digest_for_org),
        ),
        patch("app.api.internal.digest._get_default_channel", return_value=MagicMock()),
    ):
        resp = client.post(
            "/internal/digest/run",
            headers={"X-Digest-Trigger-Token": "real_secret"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["orgs_processed"] == 2
    assert body["total_events_sent"] == 2
    assert body["sent_per_org"] == {"org-a": 0, "org-b": 2}
    assert body["failed_orgs"] == []


def test_one_org_failure_does_not_abort_sweep(client: TestClient) -> None:
    """One org raising never aborts the sweep for the rest."""

    async def _fake_run_digest_for_org(org_id: str, channel: object) -> list[PendingDigestEvent]:
        if org_id == "org-a":
            raise RuntimeError("simulated DB error")
        return []

    with (
        patch(
            "app.api.internal.digest.get_settings",
            return_value=_make_mock_settings(trigger_token="real_secret"),
        ),
        patch(
            "app.api.internal.digest.list_orgs_with_daily_digest_pg",
            return_value=["org-a", "org-b"],
        ),
        patch(
            "app.api.internal.digest.run_digest_for_org",
            new=AsyncMock(side_effect=_fake_run_digest_for_org),
        ),
        patch("app.api.internal.digest._get_default_channel", return_value=MagicMock()),
    ):
        resp = client.post(
            "/internal/digest/run",
            headers={"X-Digest-Trigger-Token": "real_secret"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["orgs_processed"] == 2
    assert body["failed_orgs"] == ["org-a"]
    assert body["sent_per_org"] == {"org-b": 0}


def test_no_orgs_returns_zero_counts(client: TestClient) -> None:
    """No orgs with an enabled daily_digest preference -> zero counts, still 200."""
    with (
        patch(
            "app.api.internal.digest.get_settings",
            return_value=_make_mock_settings(trigger_token="real_secret"),
        ),
        patch(
            "app.api.internal.digest.list_orgs_with_daily_digest_pg",
            return_value=[],
        ),
        patch(
            "app.api.internal.digest.run_digest_for_org",
            new=AsyncMock(),
        ),
        patch("app.api.internal.digest._get_default_channel", return_value=MagicMock()),
    ):
        resp = client.post(
            "/internal/digest/run",
            headers={"X-Digest-Trigger-Token": "real_secret"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["orgs_processed"] == 0
    assert body["total_events_sent"] == 0
