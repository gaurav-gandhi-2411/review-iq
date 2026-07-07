"""Tests for one-click unsubscribe: token gen/verify, URL building, and the
public GET/POST /unsubscribe endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.core.alerts.unsubscribe import (
    build_unsubscribe_url,
    generate_unsubscribe_token,
    verify_unsubscribe_token,
)
from fastapi.testclient import TestClient

_SIGNING_KEY = "test-signing-key"


def _settings(
    signing_key: str = _SIGNING_KEY, base_url: str = "https://api.example.com"
) -> MagicMock:
    s = MagicMock()
    s.unsubscribe_signing_key = signing_key
    s.api_public_base_url = base_url
    return s


# ---------------------------------------------------------------------------
# Token generation / verification
# ---------------------------------------------------------------------------


def test_token_roundtrip_verifies() -> None:
    with patch("app.core.alerts.unsubscribe.get_settings", return_value=_settings()):
        token = generate_unsubscribe_token("org-1")
        assert verify_unsubscribe_token("org-1", token) is True


def test_token_rejects_wrong_org() -> None:
    with patch("app.core.alerts.unsubscribe.get_settings", return_value=_settings()):
        token = generate_unsubscribe_token("org-1")
        assert verify_unsubscribe_token("org-2", token) is False


def test_token_rejects_garbage() -> None:
    with patch("app.core.alerts.unsubscribe.get_settings", return_value=_settings()):
        assert verify_unsubscribe_token("org-1", "not-a-real-token") is False


def test_token_rejects_when_signing_key_unset() -> None:
    with patch("app.core.alerts.unsubscribe.get_settings", return_value=_settings(signing_key="")):
        assert verify_unsubscribe_token("org-1", "anything") is False


# ---------------------------------------------------------------------------
# URL building
# ---------------------------------------------------------------------------


def test_build_url_none_when_signing_key_unset() -> None:
    with patch("app.core.alerts.unsubscribe.get_settings", return_value=_settings(signing_key="")):
        assert build_unsubscribe_url("org-1") is None


def test_build_url_none_when_base_url_unset() -> None:
    with patch("app.core.alerts.unsubscribe.get_settings", return_value=_settings(base_url="")):
        assert build_unsubscribe_url("org-1") is None


def test_build_url_contains_org_and_valid_token() -> None:
    with patch("app.core.alerts.unsubscribe.get_settings", return_value=_settings()):
        url = build_unsubscribe_url("org-1")
        assert url is not None
        assert url.startswith("https://api.example.com/unsubscribe?org=org-1&token=")
        token = url.split("token=")[1]
        assert verify_unsubscribe_token("org-1", token) is True


# ---------------------------------------------------------------------------
# Public endpoint
# ---------------------------------------------------------------------------


def _client() -> TestClient:
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


def test_endpoint_valid_token_clears_notification_email() -> None:
    with patch("app.core.alerts.unsubscribe.get_settings", return_value=_settings()):
        token = generate_unsubscribe_token("org-1")

    with (
        patch("app.api.unsubscribe.get_settings", return_value=_settings()),
        patch("app.core.alerts.unsubscribe.get_settings", return_value=_settings()),
        patch("app.api.unsubscribe.set_org_notification_email_pg") as mock_clear,
    ):
        resp = _client().get("/unsubscribe", params={"org": "org-1", "token": token})

    assert resp.status_code == 200
    assert "unsubscribed" in resp.text.lower()
    mock_clear.assert_called_once_with("org-1", None)


def test_endpoint_post_one_click_also_clears() -> None:
    with patch("app.core.alerts.unsubscribe.get_settings", return_value=_settings()):
        token = generate_unsubscribe_token("org-1")

    with (
        patch("app.api.unsubscribe.get_settings", return_value=_settings()),
        patch("app.core.alerts.unsubscribe.get_settings", return_value=_settings()),
        patch("app.api.unsubscribe.set_org_notification_email_pg") as mock_clear,
    ):
        resp = _client().post("/unsubscribe", params={"org": "org-1", "token": token})

    assert resp.status_code == 200
    mock_clear.assert_called_once_with("org-1", None)


def test_endpoint_rejects_invalid_token() -> None:
    with (
        patch("app.api.unsubscribe.get_settings", return_value=_settings()),
        patch("app.api.unsubscribe.set_org_notification_email_pg") as mock_clear,
    ):
        resp = _client().get("/unsubscribe", params={"org": "org-1", "token": "wrong"})

    assert resp.status_code == 400
    mock_clear.assert_not_called()


def test_endpoint_503_when_not_configured() -> None:
    with patch("app.api.unsubscribe.get_settings", return_value=_settings(signing_key="")):
        resp = _client().get("/unsubscribe", params={"org": "org-1", "token": "anything"})

    assert resp.status_code == 503
