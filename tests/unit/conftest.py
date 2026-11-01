"""Unit-test fixtures shared across tests/unit."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

# Modules that drive the alert engine / digest senders with a mocked recipient lookup. The
# senders now also consult public.email_suppressions (bounce/complaint webhook); these tests
# have no database, and the real lookup fails closed, so it is stubbed to "not suppressed".
# Suppression behaviour itself is covered in test_resend_webhook.py.
_SENDER_TEST_MODULES = frozenset(
    {
        "test_alert_engine",
        "test_alert_wiring",
        "test_digest",
        "test_internal_digest",
        "test_urgent_coalescing",
        "test_weekly_digest",
    }
)


@pytest.fixture(autouse=True)
def _stub_email_suppression_lookup(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    if request.module.__name__.rsplit(".", 1)[-1] in _SENDER_TEST_MODULES:
        monkeypatch.setattr("app.core.alerts.storage.is_email_suppressed_pg", lambda _email: False)
    yield
