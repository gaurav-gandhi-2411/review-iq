"""Minimum-viable Stripe billing (Wave 2 P4, ADR 0008).

Deliberately thin: Stripe Checkout for signup+payment and the Stripe Customer
Portal for self-service plan changes/cancellation/dunning are Stripe's own
hosted, PCI-scope-reducing surfaces -- this module creates sessions for both
and syncs the resulting subscription state, it does not reimplement a payment
form, a plan-management UI, or custom dunning/retry logic (Stripe's own Smart
Retries + portal-driven dunning emails cover that once enabled in the Stripe
dashboard -- a GG configuration step, not code, see the PR's escalation steps).

UNVERIFIED AGAINST A LIVE STRIPE ACCOUNT -- none exists yet (no STRIPE_SECRET_KEY
configured anywhere, confirmed by grepping .env). Every Stripe SDK call below is
written to match the documented request/response shape in Stripe's own API
reference (checkout.Session, billing_portal.Session, Webhook.construct_event),
and the webhook signature verification path is unit-tested against Stripe's own
published test-vector construction (see tests/unit/test_billing.py) -- but the
actual HTTP round-trip to Stripe's API has never been exercised. Do not treat
this as "tested" until it has been run against a real Stripe test-mode account
per the escalation steps.
"""

from __future__ import annotations

from dataclasses import dataclass

import stripe
import structlog

from app.core.config import get_settings

log = structlog.get_logger(__name__)


class BillingNotConfiguredError(RuntimeError):
    """Raised when Stripe settings are missing. Distinct type so callers can
    return a clear 503 rather than a generic 500 from a Stripe SDK auth error."""


# Plan -> monthly extraction quota. Mirrors ADR 0007's proposed tiers exactly --
# change this dict AND the Stripe dashboard prices together, never one without
# the other (there is no code-level check that they still agree; see the PR's
# escalation steps for the manual reconciliation this implies).
PLAN_QUOTAS: dict[str, int] = {
    "free": 100,
    "starter": 2_000,
    "growth": 10_000,
}


@dataclass(frozen=True)
class CheckoutSession:
    checkout_url: str
    session_id: str


@dataclass(frozen=True)
class PortalSession:
    portal_url: str


def _client() -> None:
    settings = get_settings()
    if not settings.stripe_secret_key:
        raise BillingNotConfiguredError(
            "STRIPE_SECRET_KEY is not set -- billing is not configured on this deployment."
        )
    stripe.api_key = settings.stripe_secret_key


def create_checkout_session(
    *, org_id: str, plan: str, customer_email: str, existing_stripe_customer_id: str | None
) -> CheckoutSession:
    """Create a Stripe Checkout session for `plan` ('starter' or 'growth' -- 'free'
    and 'enterprise'/custom have no checkout flow, see module docstring).

    Reuses `existing_stripe_customer_id` if the org already has one (e.g. upgrading
    from Starter to Growth) rather than creating a duplicate Stripe Customer.
    """
    _client()
    settings = get_settings()
    price_id = {
        "starter": settings.stripe_price_id_starter,
        "growth": settings.stripe_price_id_growth,
    }.get(plan)
    if not price_id:
        raise ValueError(
            f"No Stripe price configured for plan={plan!r} (expected 'starter' or 'growth')"
        )
    if not settings.billing_return_url:
        raise BillingNotConfiguredError("BILLING_RETURN_URL is not set.")

    # org_id round-trips through Stripe unmodified -- the webhook handler reads it
    # back from the completed session/subscription metadata to know which org to
    # update. Never trust a client-supplied org_id at webhook time; this is the one
    # place org_id is set, server-side, before Stripe ever sees the request.
    #
    # Explicit named args (not a **kwargs dict spread) so mypy strict can check this
    # call against Stripe's own TypedDict param spec -- a dict-typed kwargs blob
    # defeats that entirely (every field reports as incompatible).
    if existing_stripe_customer_id:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=f"{settings.billing_return_url}?checkout=success",
            cancel_url=f"{settings.billing_return_url}?checkout=cancelled",
            metadata={"org_id": org_id},
            subscription_data={"metadata": {"org_id": org_id}},
            customer=existing_stripe_customer_id,
        )
    else:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=f"{settings.billing_return_url}?checkout=success",
            cancel_url=f"{settings.billing_return_url}?checkout=cancelled",
            metadata={"org_id": org_id},
            subscription_data={"metadata": {"org_id": org_id}},
            customer_email=customer_email,
        )
    log.info("billing.checkout_session_created", org_id=org_id, plan=plan, session_id=session.id)
    if session.url is None:
        # Stripe's own stub types checkout.Session.url as str | None; in practice it
        # is always populated for a newly-created session in 'subscription' mode --
        # raising rather than silently returning an unusable empty URL.
        raise RuntimeError(f"Stripe returned no checkout URL for session {session.id}")
    return CheckoutSession(checkout_url=session.url, session_id=session.id)


def create_portal_session(*, stripe_customer_id: str) -> PortalSession:
    """Create a Stripe Customer Portal session -- self-service plan change,
    cancellation, payment method update, and invoice history, all on Stripe's
    own hosted page. This is what keeps P4 "minimum viable": no custom UI for
    any of that.
    """
    _client()
    settings = get_settings()
    if not settings.billing_return_url:
        raise BillingNotConfiguredError("BILLING_RETURN_URL is not set.")
    session = stripe.billing_portal.Session.create(
        customer=stripe_customer_id,
        return_url=settings.billing_return_url,
    )
    log.info("billing.portal_session_created", stripe_customer_id=stripe_customer_id)
    return PortalSession(portal_url=session.url)


def verify_webhook_signature(*, payload: bytes, sig_header: str) -> stripe.Event:
    """Verify a Stripe webhook payload's signature and return the parsed Event.

    Raises stripe.error.SignatureVerificationError on a bad/forged signature --
    callers must reject with 400 and never process an unverified payload.
    """
    settings = get_settings()
    if not settings.stripe_webhook_secret:
        raise BillingNotConfiguredError("STRIPE_WEBHOOK_SECRET is not set.")
    # stripe.Webhook.construct_event has no type stub (untyped in the stripe package
    # itself, not a gap in our own annotations) -- the return type is documented as
    # stripe.Event in Stripe's own API reference.
    return stripe.Webhook.construct_event(  # type: ignore[no-untyped-call]
        payload, sig_header, settings.stripe_webhook_secret
    )


def plan_from_price_id(price_id: str) -> str | None:
    """Map a Stripe price ID back to this app's plan name. Returns None for an
    unrecognized price -- callers must treat that as a configuration error
    (a price was changed in Stripe without updating STRIPE_PRICE_ID_*), not
    silently default to any plan.
    """
    settings = get_settings()
    if price_id == settings.stripe_price_id_starter:
        return "starter"
    if price_id == settings.stripe_price_id_growth:
        return "growth"
    return None
