"""Unit tests for app/core/billing.py -- Wave 2 P4.

Signature verification is tested against a REAL, valid signed payload
constructed using Stripe's own documented HMAC-SHA256 scheme (the exact
algorithm implemented in stripe._webhook.WebhookSignature, itself public and
stable -- signed_payload = f"{timestamp}.{payload}",
sig = hmac_sha256(signed_payload, secret).hexdigest(), header =
f"t={timestamp},v1={sig}") -- not a live Stripe account (none exists yet, see
app/core/billing.py's module docstring), but a genuine test vector, not a mock
that assumes the verification logic works.

Checkout/portal session creation are tested with the Stripe SDK's HTTP layer
mocked (unittest.mock) -- these confirm THIS code constructs the right
request and handles the response correctly, not that Stripe's API actually
accepts it (impossible to test without a live account).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock, patch

import pytest
from app.core.billing import (
    BillingNotConfiguredError,
    create_checkout_session,
    create_portal_session,
    plan_from_price_id,
    verify_webhook_signature,
)


def _event_payload(event_type: str = "checkout.session.completed") -> bytes:
    """A minimal but shape-correct Stripe Event payload -- 'object': 'event' at
    the top level is required (Stripe's own SDK checks it, see
    stripe._webhook.Webhook.construct_event), not optional test-fixture noise."""
    return json.dumps(
        {"id": "evt_test", "object": "event", "type": event_type, "data": {"object": {}}}
    ).encode()


def _sign(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    """Construct a real Stripe-format signature header for `payload` under
    `secret`, using Stripe's own published algorithm (see module docstring)."""
    ts = timestamp if timestamp is not None else int(time.time())
    signed_payload = f"{ts}.{payload.decode()}"
    sig = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


class TestVerifyWebhookSignature:
    def test_valid_signature_is_accepted(self):
        secret = "whsec_test_secret_12345"
        payload = _event_payload("checkout.session.completed")
        header = _sign(payload, secret)

        with patch("app.core.billing.get_settings") as mock_settings:
            mock_settings.return_value.stripe_webhook_secret = secret
            event = verify_webhook_signature(payload=payload, sig_header=header)

        assert event["type"] == "checkout.session.completed"

    def test_forged_signature_is_rejected(self):
        secret = "whsec_test_secret_12345"
        payload = _event_payload()
        wrong_header = _sign(payload, "whsec_completely_different_secret")

        with patch("app.core.billing.get_settings") as mock_settings:
            mock_settings.return_value.stripe_webhook_secret = secret
            with pytest.raises(Exception, match="signature"):
                verify_webhook_signature(payload=payload, sig_header=wrong_header)

    def test_tampered_payload_is_rejected(self):
        """Signature valid for the ORIGINAL payload must reject a modified one --
        proves this isn't just checking the header is well-formed."""
        secret = "whsec_test_secret_12345"
        original = _event_payload("checkout.session.completed")
        header = _sign(original, secret)
        tampered = original.replace(b"checkout.session.completed", b"customer.subscription.deleted")

        with patch("app.core.billing.get_settings") as mock_settings:
            mock_settings.return_value.stripe_webhook_secret = secret
            with pytest.raises(Exception, match="signature"):
                verify_webhook_signature(payload=tampered, sig_header=header)

    def test_expired_timestamp_is_rejected(self):
        """Stripe's own tolerance window (default 300s) rejects a replayed old
        signature -- proves replay protection isn't silently disabled."""
        secret = "whsec_test_secret_12345"
        payload = _event_payload()
        old_header = _sign(payload, secret, timestamp=int(time.time()) - 3600)

        with patch("app.core.billing.get_settings") as mock_settings:
            mock_settings.return_value.stripe_webhook_secret = secret
            with pytest.raises(Exception):
                verify_webhook_signature(payload=payload, sig_header=old_header)

    def test_missing_webhook_secret_raises_not_configured(self):
        with patch("app.core.billing.get_settings") as mock_settings:
            mock_settings.return_value.stripe_webhook_secret = ""
            with pytest.raises(BillingNotConfiguredError):
                verify_webhook_signature(payload=b"{}", sig_header="t=1,v1=x")


class TestPlanFromPriceId:
    def test_recognizes_starter_price(self):
        with patch("app.core.billing.get_settings") as mock_settings:
            mock_settings.return_value.stripe_price_id_starter = "price_starter_123"
            mock_settings.return_value.stripe_price_id_growth = "price_growth_456"
            assert plan_from_price_id("price_starter_123") == "starter"

    def test_recognizes_growth_price(self):
        with patch("app.core.billing.get_settings") as mock_settings:
            mock_settings.return_value.stripe_price_id_starter = "price_starter_123"
            mock_settings.return_value.stripe_price_id_growth = "price_growth_456"
            assert plan_from_price_id("price_growth_456") == "growth"

    def test_unrecognized_price_returns_none_not_a_default(self):
        """A misconfigured/changed price must never silently map to some plan --
        callers treat None as a hard error, not a fallback."""
        with patch("app.core.billing.get_settings") as mock_settings:
            mock_settings.return_value.stripe_price_id_starter = "price_starter_123"
            mock_settings.return_value.stripe_price_id_growth = "price_growth_456"
            assert plan_from_price_id("price_unknown_999") is None


class TestCreateCheckoutSession:
    def test_constructs_request_with_new_customer_email(self):
        with (
            patch("app.core.billing.get_settings") as mock_settings,
            patch("app.core.billing.stripe") as mock_stripe,
        ):
            mock_settings.return_value.stripe_secret_key = "sk_test_x"
            mock_settings.return_value.stripe_price_id_starter = "price_starter_123"
            mock_settings.return_value.billing_return_url = "https://app.example.com/account"
            mock_stripe.checkout.Session.create.return_value = MagicMock(
                url="https://checkout.stripe.com/session_abc", id="cs_abc"
            )

            result = create_checkout_session(
                org_id="org-1",
                plan="starter",
                customer_email="test@example.com",
                existing_stripe_customer_id=None,
            )

            assert result.checkout_url == "https://checkout.stripe.com/session_abc"
            call_kwargs = mock_stripe.checkout.Session.create.call_args.kwargs
            assert call_kwargs["customer_email"] == "test@example.com"
            assert call_kwargs["metadata"] == {"org_id": "org-1"}
            assert call_kwargs["subscription_data"] == {"metadata": {"org_id": "org-1"}}
            assert "customer" not in call_kwargs

    def test_reuses_existing_stripe_customer(self):
        with (
            patch("app.core.billing.get_settings") as mock_settings,
            patch("app.core.billing.stripe") as mock_stripe,
        ):
            mock_settings.return_value.stripe_secret_key = "sk_test_x"
            mock_settings.return_value.stripe_price_id_growth = "price_growth_456"
            mock_settings.return_value.billing_return_url = "https://app.example.com/account"
            mock_stripe.checkout.Session.create.return_value = MagicMock(
                url="https://checkout.stripe.com/session_xyz", id="cs_xyz"
            )

            create_checkout_session(
                org_id="org-2",
                plan="growth",
                customer_email="ignored@example.com",
                existing_stripe_customer_id="cus_existing_123",
            )

            call_kwargs = mock_stripe.checkout.Session.create.call_args.kwargs
            assert call_kwargs["customer"] == "cus_existing_123"
            assert "customer_email" not in call_kwargs

    def test_unknown_plan_raises(self):
        with patch("app.core.billing.get_settings") as mock_settings:
            mock_settings.return_value.stripe_secret_key = "sk_test_x"
            mock_settings.return_value.stripe_price_id_starter = ""
            mock_settings.return_value.stripe_price_id_growth = ""
            with pytest.raises(ValueError, match="No Stripe price configured"):
                create_checkout_session(
                    org_id="org-3",
                    plan="enterprise",
                    customer_email="x@example.com",
                    existing_stripe_customer_id=None,
                )

    def test_not_configured_without_secret_key(self):
        with patch("app.core.billing.get_settings") as mock_settings:
            mock_settings.return_value.stripe_secret_key = ""
            with pytest.raises(BillingNotConfiguredError):
                create_checkout_session(
                    org_id="org-4",
                    plan="starter",
                    customer_email="x@example.com",
                    existing_stripe_customer_id=None,
                )


class TestCreatePortalSession:
    def test_constructs_request(self):
        with (
            patch("app.core.billing.get_settings") as mock_settings,
            patch("app.core.billing.stripe") as mock_stripe,
        ):
            mock_settings.return_value.stripe_secret_key = "sk_test_x"
            mock_settings.return_value.billing_return_url = "https://app.example.com/account"
            mock_stripe.billing_portal.Session.create.return_value = MagicMock(
                url="https://billing.stripe.com/portal_abc"
            )

            result = create_portal_session(stripe_customer_id="cus_123")

            assert result.portal_url == "https://billing.stripe.com/portal_abc"
            call_kwargs = mock_stripe.billing_portal.Session.create.call_args.kwargs
            assert call_kwargs["customer"] == "cus_123"
            assert call_kwargs["return_url"] == "https://app.example.com/account"
