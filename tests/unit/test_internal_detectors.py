"""Unit tests for POST /internal/detectors/run — token-protected Phase 2 detector sweep trigger.

Mirrors tests/unit/test_internal_digest.py's structure exactly. Per-org isolation logic lives
inside run_detector_sweep itself (not this endpoint), so those semantics are covered by
test_detector_sweep.py -- these tests only prove the token gate and the endpoint's delegation
to run_detector_sweep.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def _make_mock_settings(trigger_token: str = "") -> MagicMock:
    s = MagicMock()
    s.detector_sweep_trigger_token = trigger_token
    return s


@pytest.fixture
def client() -> TestClient:
    from app.main import app

    yield TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------


def test_missing_token_returns_401(client: TestClient) -> None:
    with patch(
        "app.api.internal.detectors.get_settings",
        return_value=_make_mock_settings(trigger_token="real_secret"),
    ):
        resp = client.post("/internal/detectors/run")
    assert resp.status_code == 401


def test_wrong_token_returns_401(client: TestClient) -> None:
    with patch(
        "app.api.internal.detectors.get_settings",
        return_value=_make_mock_settings(trigger_token="real_secret"),
    ):
        resp = client.post(
            "/internal/detectors/run",
            headers={"X-Detector-Sweep-Token": "wrong_token"},
        )
    assert resp.status_code == 401


def test_unconfigured_server_returns_503(client: TestClient) -> None:
    """detector_sweep_trigger_token empty/unset -> 503, even with some header value provided."""
    with patch(
        "app.api.internal.detectors.get_settings",
        return_value=_make_mock_settings(trigger_token=""),
    ):
        resp = client.post(
            "/internal/detectors/run",
            headers={"X-Detector-Sweep-Token": "anything"},
        )
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Sweep delegation
# ---------------------------------------------------------------------------


def test_correct_token_runs_sweep_and_returns_result(client: TestClient) -> None:
    fake_result = {
        "batch_defect": {"orgs": 2, "sent": 1, "failed_orgs": []},
        "fake_campaign": {"orgs": 2, "sent": 0, "failed_orgs": ["org-b"]},
    }
    with (
        patch(
            "app.api.internal.detectors.get_settings",
            return_value=_make_mock_settings(trigger_token="real_secret"),
        ),
        patch(
            "app.api.internal.detectors.run_detector_sweep",
            new=AsyncMock(return_value=fake_result),
        ) as mock_sweep,
        patch("app.api.internal.detectors._get_default_channel", return_value=MagicMock()),
    ):
        resp = client.post(
            "/internal/detectors/run",
            headers={"X-Detector-Sweep-Token": "real_secret"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["batch_defect"] == fake_result["batch_defect"]
    assert body["fake_campaign"] == fake_result["fake_campaign"]
    mock_sweep.assert_awaited_once()
