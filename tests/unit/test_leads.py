"""POST /leads -- marketing-site lead capture (Session 15c C9).

No real DB, no real Resend: the two storage functions and resend.Emails.send_async are
mocked at their call sites in app.api.leads, exactly like the demo/alert unit tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.api import leads as leads_module
from fastapi.testclient import TestClient

MARKETING_ORIGIN = "https://samidhareviews.xyz"
NOTIFY = ["hello@samidhareviews.xyz"]

_VALID: dict[str, Any] = {
    "name": "Asha Rao",
    "email": "Asha.Rao@Example.com",
    "company": "Rao Home Appliances",
    "brands": "Rao, Rao Pro",
    "reviews_per_month": "1000-5000",
    "message": "We sell on Amazon and Flipkart.\nCan you do Hindi reviews?",
    "website": "",
}


def _settings(**overrides: Any) -> SimpleNamespace:
    base = {
        "resend_api_key": "re_test_key",
        "resend_from_email": "hello@samidhareviews.xyz",
        "resend_from_name": "Samidha Reviews",
        "leads_notify_email": "hello@samidhareviews.xyz",
        "leads_ip_hash_salt": "unit-test-salt",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _Mocks:
    def __init__(self, insert: MagicMock, update: MagicMock, send: AsyncMock) -> None:
        self.insert = insert
        self.update = update
        self.send = send


@pytest.fixture
def mocks() -> Iterator[_Mocks]:
    with (
        patch("app.api.leads.get_settings", return_value=_settings()),
        patch("app.api.leads.insert_lead_pg") as insert,
        patch("app.api.leads.update_lead_email_status_pg", return_value=True) as update,
        patch("resend.Emails.send_async", new=AsyncMock(return_value={"id": "msg-1"})) as send,
    ):
        yield _Mocks(insert, update, send)


@pytest.fixture
def client() -> TestClient:
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


def _post(client: TestClient, body: dict[str, Any], **kwargs: Any) -> Any:
    return client.post("/leads", json=body, **kwargs)


def test_valid_submit_persists_and_sends_two_emails(client: TestClient, mocks: _Mocks) -> None:
    resp = _post(client, _VALID, headers={"User-Agent": "pytest-agent/1.0"})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    mocks.insert.assert_called_once()
    args = mocks.insert.call_args.args
    _lead_id, name, email, company, brands, rpm, message, ip_hash, user_agent = args
    assert (name, company, brands, rpm) == (
        "Asha Rao",
        "Rao Home Appliances",
        "Rao, Rao Pro",
        "1000-5000",
    )
    assert email == "asha.rao@example.com"  # lowercased for storage/reply-to
    assert message is not None and "Hindi" in message
    assert user_agent == "pytest-agent/1.0"
    # Raw client IP must never be persisted -- only the keyed hash (64 hex chars).
    assert ip_hash is not None and len(ip_hash) == 64
    assert "testclient" not in {str(a) for a in args}

    assert mocks.send.await_count == 2
    sent = [c.args[0] for c in mocks.send.await_args_list]
    notification = next(p for p in sent if p["to"] == NOTIFY)
    confirmation = next(p for p in sent if p["to"] == ["asha.rao@example.com"])
    assert notification["reply_to"] == ["asha.rao@example.com"]
    for field in ("Asha Rao", "Rao Home Appliances", "Rao, Rao Pro", "1000-5000", "Hindi"):
        assert field in notification["text"]
    assert confirmation["reply_to"] == NOTIFY
    assert "Asha" not in confirmation["text"]  # no submitter free-text echoed to a third party

    mocks.update.assert_called_once()
    assert mocks.update.call_args.args[1] == "sent"


def test_message_is_optional(client: TestClient, mocks: _Mocks) -> None:
    body = {k: v for k, v in _VALID.items() if k != "message"}
    assert _post(client, body).status_code == 200
    assert mocks.insert.call_args.args[6] is None


def test_ip_hash_is_null_without_server_secret(client: TestClient, mocks: _Mocks) -> None:
    with patch("app.api.leads.get_settings", return_value=_settings(leads_ip_hash_salt="")):
        assert _post(client, _VALID).status_code == 200
    assert mocks.insert.call_args.args[7] is None


def test_honeypot_returns_ok_but_persists_and_sends_nothing(
    client: TestClient, mocks: _Mocks
) -> None:
    resp = _post(client, {**_VALID, "website": "http://spam.example"})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}  # identical shape to a real success
    mocks.insert.assert_not_called()
    mocks.update.assert_not_called()
    mocks.send.assert_not_awaited()


def test_honeypot_hides_validation_failures_too(client: TestClient, mocks: _Mocks) -> None:
    """A bot that fills the honeypot AND sends junk must not learn it was detected."""
    resp = _post(client, {"email": "not-an-email", "website": "x"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mocks.insert.assert_not_called()


@pytest.mark.parametrize(
    "bad_email",
    ["not-an-email", "a@b", "a b@example.com", "@example.com", "a@@example.com", "a@exa mple.com"],
)
def test_invalid_email_is_422_with_machine_code(
    client: TestClient, mocks: _Mocks, bad_email: str
) -> None:
    resp = _post(client, {**_VALID, "email": bad_email})
    assert resp.status_code == 422
    body = resp.json()
    assert body["ok"] is False
    assert body["error"] == "invalid_email"
    assert "valid address" in body["message"]
    mocks.insert.assert_not_called()
    mocks.send.assert_not_awaited()


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("name", 100),
        ("company", 150),
        ("brands", 500),
        ("reviews_per_month", 50),
        ("message", 4000),
    ],
)
def test_oversize_field_is_422(client: TestClient, mocks: _Mocks, field: str, limit: int) -> None:
    resp = _post(client, {**_VALID, field: "x" * (limit + 1)})
    assert resp.status_code == 422
    assert resp.json()["error"] == "field_too_long"
    assert field in resp.json()["message"]
    mocks.insert.assert_not_called()

    # Boundary: exactly at the limit is accepted.
    assert _post(client, {**_VALID, field: "x" * limit}).status_code == 200


def test_oversize_email_is_422(client: TestClient, mocks: _Mocks) -> None:
    resp = _post(client, {**_VALID, "email": "a" * 250 + "@example.com"})
    assert resp.status_code == 422
    assert resp.json()["error"] == "field_too_long"


def test_missing_and_blank_required_fields_are_422(client: TestClient, mocks: _Mocks) -> None:
    body = {k: v for k, v in _VALID.items() if k != "company"}
    resp = _post(client, body)
    assert resp.status_code == 422
    assert resp.json()["error"] == "field_required"

    resp = _post(client, {**_VALID, "name": "   "})
    assert resp.status_code == 422
    assert resp.json()["error"] == "field_required"


def test_non_string_field_is_422(client: TestClient, mocks: _Mocks) -> None:
    resp = _post(client, {**_VALID, "reviews_per_month": 3000})
    assert resp.status_code == 422
    assert resp.json()["error"] == "invalid_field"


@pytest.mark.parametrize("field", ["name", "company", "brands", "reviews_per_month", "email"])
@pytest.mark.parametrize("payload", ["Bob\r\nBcc: evil@example.com", "Bob\nX-Header: 1", "a\x00b"])
def test_crlf_and_control_chars_rejected_in_single_line_fields(
    client: TestClient, mocks: _Mocks, field: str, payload: str
) -> None:
    value = f"a@example.com{payload}" if field == "email" else payload
    resp = _post(client, {**_VALID, field: value})
    assert resp.status_code == 422
    assert resp.json()["error"] in {"invalid_characters", "invalid_email"}
    mocks.insert.assert_not_called()
    mocks.send.assert_not_awaited()


def test_unicode_line_separator_and_bidi_override_rejected_in_name(
    client: TestClient, mocks: _Mocks
) -> None:
    for bad in ("Bob Bcc: x", "Bob‮evil"):
        resp = _post(client, {**_VALID, "name": bad})
        assert resp.status_code == 422
        assert resp.json()["error"] == "invalid_characters"


def test_message_allows_newlines_but_not_other_controls(client: TestClient, mocks: _Mocks) -> None:
    assert _post(client, {**_VALID, "message": "line1\r\nline2\tend"}).status_code == 200
    resp = _post(client, {**_VALID, "message": "bad\x07bell"})
    assert resp.status_code == 422
    assert resp.json()["error"] == "invalid_characters"


def test_non_json_and_non_object_bodies_are_422(client: TestClient, mocks: _Mocks) -> None:
    resp = client.post("/leads", content=b"not json", headers={"Content-Type": "application/json"})
    assert resp.status_code == 422
    assert resp.json()["error"] == "invalid_json"
    resp = client.post("/leads", json=["a", "list"])
    assert resp.status_code == 422
    assert resp.json()["error"] == "invalid_json"


def test_oversize_body_is_422(client: TestClient, mocks: _Mocks) -> None:
    resp = _post(client, {**_VALID, "message": "x" * (20 * 1024)})
    assert resp.status_code == 422
    assert resp.json()["error"] == "payload_too_large"
    mocks.insert.assert_not_called()


def test_per_ip_rate_limit_returns_429_in_contract_shape(client: TestClient, mocks: _Mocks) -> None:
    for i in range(5):
        assert _post(client, _VALID).status_code == 200, f"request {i + 1}"
    resp = _post(client, _VALID)

    assert resp.status_code == 429
    body = resp.json()
    assert body["ok"] is False
    assert body["error"] == "rate_limited"
    assert "hello@samidhareviews.xyz" in body["message"]
    assert resp.headers["retry-after"]
    assert mocks.insert.call_count == 5  # the 6th never reached the handler body


def test_global_cap_blocks_a_botnet_of_distinct_ips(mocks: _Mocks) -> None:
    from app.main import app

    for i in range(10):
        c = TestClient(app, raise_server_exceptions=False, client=(f"203.0.113.{i + 1}", 5000))
        assert _post(c, _VALID).status_code == 200, f"ip {i + 1}"
    attacker = TestClient(app, raise_server_exceptions=False, client=("198.51.100.99", 5000))
    resp = _post(attacker, _VALID)
    assert resp.status_code == 429
    assert resp.json()["error"] == "rate_limited"


def test_other_routes_keep_the_default_429_shape() -> None:
    """The contract-shaped 429 is /leads-only; every other route's handler is unchanged."""
    from app.main import app

    c = TestClient(app, raise_server_exceptions=False)
    # /metrics is covered only by the 30/minute default limit; exceed it.
    codes = [c.get("/metrics").status_code for _ in range(35)]
    assert 429 in codes
    resp = c.get("/metrics")
    assert resp.status_code == 429
    assert "ok" not in resp.json()


def test_resend_failure_still_returns_ok_and_records_failed(
    client: TestClient, mocks: _Mocks
) -> None:
    mocks.send.side_effect = RuntimeError("resend is down")
    resp = _post(client, _VALID)

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mocks.insert.assert_called_once()  # persistence is independent of email
    mocks.update.assert_called_once()
    assert mocks.update.call_args.args[1] == "failed"


def test_one_email_failing_records_partial(client: TestClient, mocks: _Mocks) -> None:
    async def flaky(params: dict[str, Any]) -> dict[str, str]:
        if params["to"] == NOTIFY:
            raise RuntimeError("boom")
        return {"id": "ok"}

    mocks.send.side_effect = flaky
    assert _post(client, _VALID).status_code == 200
    assert mocks.update.call_args.args[1] == "partial"


def test_resend_timeout_is_bounded_and_does_not_fail_the_request(
    client: TestClient, mocks: _Mocks
) -> None:
    async def hang(_params: dict[str, Any]) -> None:
        await asyncio.sleep(5)

    mocks.send.side_effect = hang
    with patch.object(leads_module, "_EMAIL_SEND_TIMEOUT_S", 0.05):
        resp = _post(client, _VALID)
    assert resp.status_code == 200
    assert mocks.update.call_args.args[1] == "failed"


def test_resend_unconfigured_records_skipped(client: TestClient, mocks: _Mocks) -> None:
    with patch("app.api.leads.get_settings", return_value=_settings(resend_api_key="")):
        resp = _post(client, _VALID)
    assert resp.status_code == 200
    mocks.send.assert_not_awaited()
    assert mocks.update.call_args.args[1] == "skipped"


def test_email_failure_logs_no_pii(client: TestClient, mocks: _Mocks) -> None:
    mocks.send.side_effect = RuntimeError("to asha.rao@example.com rejected")
    with patch.object(leads_module, "log") as log:
        assert _post(client, _VALID).status_code == 200
    logged = repr(log.method_calls)
    assert "asha.rao@example.com" not in logged.lower()
    assert "Asha" not in logged
    assert "leads.email_failed" in logged


def test_db_down_but_emails_sent_is_still_ok(client: TestClient, mocks: _Mocks) -> None:
    mocks.insert.side_effect = RuntimeError("db down")
    resp = _post(client, _VALID)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mocks.update.assert_not_called()
    notification = next(c.args[0] for c in mocks.send.await_args_list if c.args[0]["to"] == NOTIFY)
    assert "NOT stored" in notification["text"]


def test_db_down_and_emails_down_is_503_not_a_silent_loss(
    client: TestClient, mocks: _Mocks
) -> None:
    mocks.insert.side_effect = RuntimeError("db down")
    mocks.send.side_effect = RuntimeError("resend down")
    resp = _post(client, _VALID)
    assert resp.status_code == 503
    assert resp.json()["error"] == "temporarily_unavailable"
    assert resp.json()["ok"] is False


def test_status_update_failure_does_not_fail_the_request(client: TestClient, mocks: _Mocks) -> None:
    mocks.update.side_effect = RuntimeError("db blip")
    assert _post(client, _VALID).status_code == 200


# --- CORS: /leads-only allowlist, never global -------------------------------------


def test_cors_allowed_origin_gets_header_on_post(client: TestClient, mocks: _Mocks) -> None:
    for origin in ("https://samidhareviews.xyz", "https://www.samidhareviews.xyz"):
        resp = _post(client, _VALID, headers={"Origin": origin})
        assert resp.status_code == 200
        assert resp.headers["access-control-allow-origin"] == origin


def test_cors_evil_origin_gets_no_header_on_post(client: TestClient, mocks: _Mocks) -> None:
    resp = _post(client, _VALID, headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in resp.headers


def test_cors_preflight_allowed_and_denied(client: TestClient, mocks: _Mocks) -> None:
    preflight = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    }
    ok = client.options("/leads", headers={"Origin": MARKETING_ORIGIN, **preflight})
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == MARKETING_ORIGIN
    assert "POST" in ok.headers["access-control-allow-methods"]

    bad = client.options("/leads", headers={"Origin": "https://evil.example", **preflight})
    assert bad.status_code == 400
    assert "access-control-allow-origin" not in bad.headers


def test_cors_marketing_origin_is_not_allowed_on_any_other_route(client: TestClient) -> None:
    """The marketing origin must gain nothing outside /leads (CORS not opened globally)."""
    preflight = {
        "Origin": MARKETING_ORIGIN,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization",
    }
    other = client.options("/v2/extract", headers=preflight)
    assert "access-control-allow-origin" not in other.headers
    health = client.get("/health", headers={"Origin": MARKETING_ORIGIN})
    assert "access-control-allow-origin" not in health.headers


def test_path_scoped_cors_refuses_wildcard() -> None:
    from app.core.cors import PathScopedCORSMiddleware

    with pytest.raises(ValueError, match="must not allow"):
        PathScopedCORSMiddleware(
            MagicMock(), path="/x", allow_origins=["*"], allow_methods=["POST"], allow_headers=[]
        )
