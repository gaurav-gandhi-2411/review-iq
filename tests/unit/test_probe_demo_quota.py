"""Unit tests for scripts/probe_demo_quota.py's response classification.

Session 12 P7b: the workflow that consumes this script's exit code branches on
0 (ok) vs 2 (quota-exhausted, debounce candidate) vs 1 (anything else, immediate
alert) -- these tests pin that classification so a future change can't silently
merge the debounced and non-debounced failure paths.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from scripts.probe_demo_quota import (
    EXIT_HARD_FAILURE,
    EXIT_OK,
    EXIT_QUOTA_EXHAUSTED,
    probe_demo_endpoint,
)


def _mock_response(status_code: int, json_body: dict | None = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    resp.text = text
    return resp


def test_200_with_expected_body_is_ok() -> None:
    with patch(
        "scripts.probe_demo_quota.httpx.post",
        return_value=_mock_response(200, {"sentiment": "positive"}),
    ):
        result = probe_demo_endpoint("https://example.test/demo/extract")
    assert result.exit_code == EXIT_OK
    assert result.status_code == 200


def test_200_with_unexpected_body_is_a_hard_failure() -> None:
    """A 200 that doesn't look like a real extraction is a real bug, not a pass --
    e.g. a misconfigured proxy silently swallowing the request and returning 200."""
    with patch(
        "scripts.probe_demo_quota.httpx.post",
        return_value=_mock_response(200, {"unexpected": "shape"}),
    ):
        result = probe_demo_endpoint("https://example.test/demo/extract")
    assert result.exit_code == EXIT_HARD_FAILURE


def test_429_is_quota_exhausted_not_a_hard_failure() -> None:
    """The whole point of this probe: a 429 must NOT exit the same way a real
    outage would -- the calling workflow debounces this across one probe interval."""
    with patch("scripts.probe_demo_quota.httpx.post", return_value=_mock_response(429)):
        result = probe_demo_endpoint("https://example.test/demo/extract")
    assert result.exit_code == EXIT_QUOTA_EXHAUSTED
    assert result.status_code == 429


def test_5xx_is_a_hard_failure() -> None:
    with patch(
        "scripts.probe_demo_quota.httpx.post",
        return_value=_mock_response(503, text="upstream LLM unavailable"),
    ):
        result = probe_demo_endpoint("https://example.test/demo/extract")
    assert result.exit_code == EXIT_HARD_FAILURE
    assert result.status_code == 503


def test_connection_error_is_a_hard_failure() -> None:
    with patch(
        "scripts.probe_demo_quota.httpx.post",
        side_effect=ConnectionError("connection refused"),
    ):
        result = probe_demo_endpoint("https://example.test/demo/extract")
    assert result.exit_code == EXIT_HARD_FAILURE
    assert result.status_code is None
