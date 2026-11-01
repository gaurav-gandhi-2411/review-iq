"""Tests for POST /webhooks/resend (Svix-signed bounce/complaint receiver) and the sender-side
suppression guard. No database: storage calls are patched."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock, patch

import psycopg2.errors
import pytest
from app.api.webhooks import resend as hook
from app.core.alerts import storage
from app.core.alerts.channels.fake import FakeChannel
from app.core.alerts.digest import PendingDigestEvent, run_digest_for_org
from app.core.alerts.engine import evaluate_and_alert
from app.core.alerts.rules import AlertEvent, AlertEventType
from fastapi.testclient import TestClient

_KEY = b"unit-test-signing-key-0123456789"
_SECRET = "whsec_" + base64.b64encode(_KEY).decode()
_ADDR = "Seller@Example.com"


def _sign(body: bytes, *, msg_id: str = "msg_1", ts: int | None = None, key: bytes = _KEY):
    ts = int(time.time()) if ts is None else ts
    signed = f"{msg_id}.{ts}.".encode() + body
    sig = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return {"svix-id": msg_id, "svix-timestamp": str(ts), "svix-signature": f"v1,{sig}"}


def _event(kind: str, bounce_type: str | None = "Permanent", to=None) -> bytes:
    data: dict = {"email_id": "e1", "to": [_ADDR] if to is None else to}
    if bounce_type is not None:
        data["bounce"] = {"type": bounce_type, "subType": "General", "message": "x"}
    return json.dumps({"type": kind, "created_at": "2026-10-09T00:00:00Z", "data": data}).encode()


def _client() -> TestClient:
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def secret_set():
    with patch.object(hook, "get_settings", return_value=MagicMock(resend_webhook_secret=_SECRET)):
        yield


@pytest.fixture
def db():
    with (
        patch.object(hook, "resolve_orgs_for_notification_email_pg", return_value=["org-1"]) as r,
        patch.object(hook, "set_org_notification_email_pg") as clear,
        patch.object(hook, "record_email_suppression_pg", return_value=True) as rec,
    ):
        yield MagicMock(resolve=r, clear=clear, record=rec)


def _post(body: bytes, headers: dict | None = None, content_type="application/json"):
    h = _sign(body) if headers is None else headers
    return _client().post(
        "/webhooks/resend", content=body, headers={**h, "content-type": content_type}
    )


# ---- fail closed / signature ------------------------------------------------------------


def test_unset_secret_returns_503_and_processes_nothing(db) -> None:
    body = _event("email.bounced")
    with patch.object(hook, "get_settings", return_value=MagicMock(resend_webhook_secret="")):
        resp = _post(body)
    assert resp.status_code == 503
    db.resolve.assert_not_called()
    db.record.assert_not_called()


def test_malformed_secret_returns_503(db) -> None:
    with patch.object(
        hook, "get_settings", return_value=MagicMock(resend_webhook_secret="whsec_!!")
    ):
        assert _post(_event("email.bounced")).status_code == 503
    db.record.assert_not_called()


def test_valid_hard_bounce_clears_notification_email_and_records(secret_set, db) -> None:
    resp = _post(_event("email.bounced"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "processed"
    db.resolve.assert_called_once_with("seller@example.com")
    db.clear.assert_called_once_with("org-1", None)  # the unsubscribe choke point
    db.record.assert_called_once_with("msg_1#0", "seller@example.com", "hard_bounce")


def test_complaint_clears_and_records(secret_set, db) -> None:
    resp = _post(_event("email.complained", bounce_type=None))
    assert resp.status_code == 200
    db.clear.assert_called_once_with("org-1", None)
    db.record.assert_called_once_with("msg_1#0", "seller@example.com", "complaint")


def test_bad_signature_is_401_and_nothing_processed(secret_set, db) -> None:
    body = _event("email.bounced")
    resp = _post(body, _sign(body, key=b"some-other-key"))
    assert resp.status_code == 401
    db.resolve.assert_not_called()
    db.record.assert_not_called()


def test_tampered_body_is_401(secret_set, db) -> None:
    headers = _sign(_event("email.bounced"))
    assert _post(_event("email.bounced", to=["victim@example.com"]), headers).status_code == 401
    db.clear.assert_not_called()


def test_missing_headers_is_401(secret_set, db) -> None:
    assert _post(_event("email.bounced"), {}).status_code == 401
    db.record.assert_not_called()


@pytest.mark.parametrize("skew", [-301, 301])
def test_stale_or_future_timestamp_is_401(secret_set, db, skew: int) -> None:
    body = _event("email.bounced")
    resp = _post(body, _sign(body, ts=int(time.time()) + skew))
    assert resp.status_code == 401
    db.record.assert_not_called()


def test_non_numeric_timestamp_is_401(secret_set, db) -> None:
    body = _event("email.bounced")
    headers = {**_sign(body), "svix-timestamp": "yesterday"}
    assert _post(body, headers).status_code == 401


def test_multiple_signatures_one_valid_is_accepted(secret_set, db) -> None:
    body = _event("email.bounced")
    h = _sign(body)
    h["svix-signature"] = "v1,AAAA " + h["svix-signature"]
    assert _post(body, h).status_code == 200


def test_replayed_event_id_is_idempotent(secret_set, db) -> None:
    body = _event("email.bounced")
    headers = _sign(body)
    assert _post(body, headers).status_code == 200
    db.record.return_value = False  # second delivery: UNIQUE(event_id) makes it a no-op
    assert _post(body, headers).status_code == 200
    assert {c.args[0] for c in db.record.call_args_list} == {"msg_1#0"}
    assert db.clear.call_count == 2  # clearing is idempotent; both calls set NULL


# ---- event routing ----------------------------------------------------------------------


def test_soft_bounce_is_logged_only(secret_set, db) -> None:
    resp = _post(_event("email.bounced", bounce_type="Transient"))
    assert resp.status_code == 200 and resp.json()["status"] == "logged"
    db.clear.assert_not_called()
    db.record.assert_not_called()


def test_delivery_delayed_is_logged_only(secret_set, db) -> None:
    resp = _post(_event("email.delivery_delayed", bounce_type=None))
    assert resp.status_code == 200 and resp.json()["status"] == "logged"
    db.record.assert_not_called()


def test_unknown_event_type_ignored_with_200(secret_set, db) -> None:
    resp = _post(_event("email.opened", bounce_type=None))
    assert resp.status_code == 200 and resp.json()["status"] == "ignored"
    db.record.assert_not_called()


def test_address_not_mapped_to_any_org_is_still_recorded(secret_set, db) -> None:
    db.resolve.return_value = []
    assert _post(_event("email.complained", bounce_type=None)).status_code == 200
    db.clear.assert_not_called()
    db.record.assert_called_once()


def test_multiple_recipients_get_distinct_event_ids(secret_set, db) -> None:
    body = _event("email.bounced", to=["a@x.com", "b@x.com", "not-an-email", 7])
    assert _post(body).status_code == 200
    assert [c.args[0] for c in db.record.call_args_list] == ["msg_1#0", "msg_1#1"]


def test_full_address_is_never_logged(secret_set, db, capsys) -> None:
    _post(_event("email.bounced"))
    out = capsys.readouterr()
    assert "seller@example.com" not in (out.out + out.err).lower()


# ---- hostile payloads -------------------------------------------------------------------


def test_huge_body_rejected_with_413_before_processing(secret_set, db) -> None:
    body = b"{" + b" " * (hook._MAX_BODY_BYTES + 10) + b"}"
    assert _post(body).status_code == 413
    db.record.assert_not_called()


def test_malformed_json_with_valid_signature_is_400(secret_set, db) -> None:
    assert _post(b"{not json").status_code == 400
    db.record.assert_not_called()


def test_non_object_json_is_400(secret_set, db) -> None:
    assert _post(b"[1,2,3]").status_code == 400


def test_wrong_content_type_is_415(secret_set, db) -> None:
    assert _post(_event("email.bounced"), content_type="text/plain").status_code == 415
    db.record.assert_not_called()


def test_non_utf8_body_is_400(secret_set, db) -> None:
    assert _post(b"\xff\xfe\x00").status_code == 400


def test_event_without_data_is_ignored_safely(secret_set, db) -> None:
    body = json.dumps({"type": "email.bounced", "data": "oops"}).encode()
    assert _post(body).status_code == 200
    db.record.assert_not_called()


# ---- unit: signature helper + route is mounted -------------------------------------------


def test_verify_signature_pure_function() -> None:
    body = b"{}"
    h = _sign(body, ts=1_000)
    kwargs = {
        "secret": _SECRET,
        "svix_id": h["svix-id"],
        "svix_timestamp": h["svix-timestamp"],
        "svix_signature": h["svix-signature"],
        "body": body,
    }
    assert hook.verify_svix_signature(**kwargs, now=1_100) is True
    assert hook.verify_svix_signature(**kwargs, now=1_000 + 301) is False


def test_route_is_mounted_on_public_service() -> None:
    # A POST must reach the handler (503: no secret configured), not 404.
    with patch.object(hook, "get_settings", return_value=MagicMock(resend_webhook_secret="")):
        assert _client().post("/webhooks/resend", content=b"{}").status_code == 503


# ---- sender-side suppression --------------------------------------------------------------


def _high_urgency_event() -> AlertEvent:
    return AlertEvent(event_type=AlertEventType.HIGH_URGENCY, details={"summary": "s"})


async def test_digest_skips_suppressed_recipient_and_records_nothing() -> None:
    pe = PendingDigestEvent(review_id="r1", event=_high_urgency_event())
    channel = FakeChannel()
    with (
        patch("app.core.alerts.digest.collect_pending_for_org", return_value=[pe]),
        patch("app.core.alerts.digest.get_org_notification_email_pg", return_value=_ADDR),
        patch("app.core.alerts.storage.is_email_suppressed_pg", return_value=True),
        patch("app.core.alerts.digest.record_alert_sent_pg") as rec,
    ):
        assert await run_digest_for_org("org-1", channel) == []
    assert channel.sent == []
    rec.assert_not_called()


async def test_engine_skips_suppressed_recipient() -> None:
    channel = FakeChannel()
    with (
        patch("app.core.alerts.engine.is_already_alerted_pg", return_value=False),
        patch("app.core.alerts.engine.get_preference_pg", return_value=None),
        patch("app.core.alerts.storage.is_email_suppressed_pg", return_value=True),
        patch("app.core.alerts.engine.record_alert_sent_pg") as rec,
    ):
        sent = await evaluate_and_alert(
            org_id="org-1",
            review_id="r1",
            extraction=None,
            auth=None,
            channel=channel,
            recipient_email=_ADDR,
            precomputed_events=[_high_urgency_event()],
        )
    assert sent == [] and channel.sent == []
    rec.assert_not_called()


async def test_recipient_guard_fails_closed_on_db_error() -> None:
    with patch("app.core.alerts.storage.is_email_suppressed_pg", side_effect=RuntimeError("db")):
        assert await storage.recipient_is_suppressed(_ADDR, org_id="o") is True


async def test_recipient_guard_tolerates_unmigrated_database() -> None:
    err = psycopg2.errors.UndefinedFunction("no such function")
    with patch("app.core.alerts.storage.is_email_suppressed_pg", side_effect=err):
        assert await storage.recipient_is_suppressed(_ADDR, org_id="o") is False
