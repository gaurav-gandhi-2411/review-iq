"""POST /webhooks/stripe -- subscription lifecycle sync (Wave 2 P4, ADR 0008).

UNVERIFIED AGAINST A LIVE STRIPE ACCOUNT -- see app/core/billing.py's module
docstring. Signature verification itself is unit-tested against Stripe's own
published test-vector construction (tests/unit/test_billing.py), but no event
here has ever been received from Stripe for real.

Handles the minimal event set a subscription lifecycle needs:
  - checkout.session.completed  -> first-time subscribe: store customer_id,
    subscription_id, set plan + quota.
  - customer.subscription.updated -> plan change (upgrade/downgrade via the
    Customer Portal) or status change (e.g. active -> past_due).
  - customer.subscription.deleted -> cancellation (self-service via Portal, or
    Stripe auto-canceling after exhausting Smart Retries) -> revert to free.
  - invoice.payment_failed -> logged for visibility only. Stripe's own Smart
    Retries (dashboard-configured, not this code) handle the actual retry
    schedule and dunning emails -- this handler does not reimplement that.

Every event is matched to an org via metadata.org_id (set server-side at
checkout creation, never client-controlled -- see billing.create_checkout_session)
or, when an event doesn't carry it directly, via the stripe_customer_id lookup
(get_org_id_for_stripe_customer_pg). An event whose org can't be resolved is
logged and dropped -- never guessed, never applied to a default org.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Header, HTTPException, Request, status

from app.core.billing import plan_from_price_id, verify_webhook_signature
from app.core.billing_storage import (
    downgrade_to_free_pg,
    get_org_id_for_stripe_customer_pg,
    sync_subscription_pg,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/webhooks/stripe", tags=["webhooks"])


def _epoch_to_datetime(epoch: int | None) -> datetime | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=UTC)


async def _handle_checkout_completed(event_data: dict[str, Any]) -> None:
    session = event_data["object"]
    org_id = (session.get("metadata") or {}).get("org_id")
    if not org_id:
        log.error("stripe_webhook.checkout_completed_missing_org_id", session_id=session.get("id"))
        return

    customer_id = session["customer"]
    subscription_id = session.get("subscription")
    # Checkout's own payload doesn't carry the price/plan directly -- the
    # subscription.created event (which Stripe also fires) does, but to keep
    # this handler self-contained rather than depending on event ordering,
    # the line_items would need a separate retrieve call. Deferred: for now,
    # rely on the subsequent customer.subscription.updated event (which Stripe
    # fires immediately after checkout.session.completed) to set plan/quota --
    # this handler only persists the customer/subscription IDs.
    sync_subscription_pg(
        org_id=org_id,
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        plan="free",  # placeholder until subscription.updated fills in the real plan
        subscription_status="incomplete",
        current_period_end=None,
    )
    log.info("stripe_webhook.checkout_completed", org_id=org_id, customer_id=customer_id)


async def _handle_subscription_updated(event_data: dict[str, Any]) -> None:
    sub = event_data["object"]
    org_id = (sub.get("metadata") or {}).get("org_id")
    if not org_id:
        customer_id = sub.get("customer")
        org_id = get_org_id_for_stripe_customer_pg(customer_id) if customer_id else None
    if not org_id:
        log.error(
            "stripe_webhook.subscription_updated_unresolved_org", subscription_id=sub.get("id")
        )
        return

    items = (sub.get("items") or {}).get("data") or []
    price_id = items[0]["price"]["id"] if items else None
    plan = plan_from_price_id(price_id) if price_id else None
    if plan is None:
        log.error(
            "stripe_webhook.unrecognized_price",
            subscription_id=sub.get("id"),
            price_id=price_id,
            org_id=org_id,
        )
        return

    sync_subscription_pg(
        org_id=org_id,
        stripe_customer_id=sub["customer"],
        stripe_subscription_id=sub["id"],
        plan=plan,
        subscription_status=sub.get("status"),
        current_period_end=_epoch_to_datetime(sub.get("current_period_end")),
    )
    log.info(
        "stripe_webhook.subscription_updated", org_id=org_id, plan=plan, status=sub.get("status")
    )


async def _handle_subscription_deleted(event_data: dict[str, Any]) -> None:
    sub = event_data["object"]
    customer_id = sub.get("customer")
    org_id = (sub.get("metadata") or {}).get("org_id") or (
        get_org_id_for_stripe_customer_pg(customer_id) if customer_id else None
    )
    if not org_id or not customer_id:
        log.error(
            "stripe_webhook.subscription_deleted_unresolved_org", subscription_id=sub.get("id")
        )
        return
    downgrade_to_free_pg(org_id=org_id, stripe_customer_id=customer_id)
    log.info("stripe_webhook.subscription_deleted", org_id=org_id)


async def _handle_payment_failed(event_data: dict[str, Any]) -> None:
    invoice = event_data["object"]
    # Visibility only -- Stripe's own Smart Retries handle the retry schedule
    # and dunning emails (dashboard-configured). subscription_status will
    # reflect 'past_due' via the subscription.updated event Stripe also fires.
    log.warning(
        "stripe_webhook.payment_failed",
        customer_id=invoice.get("customer"),
        invoice_id=invoice.get("id"),
        attempt_count=invoice.get("attempt_count"),
    )


_HANDLERS = {
    "checkout.session.completed": _handle_checkout_completed,
    "customer.subscription.updated": _handle_subscription_updated,
    "customer.subscription.deleted": _handle_subscription_deleted,
    "invoice.payment_failed": _handle_payment_failed,
}


@router.post("", status_code=status.HTTP_200_OK)
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(default="", alias="Stripe-Signature"),
) -> dict[str, str]:
    """Receive a Stripe webhook event. Signature verified before any parsing --
    an invalid/forged signature is rejected with 401 before the payload is
    touched. Unrecognized event types are accepted (200) and ignored -- Stripe
    retries on non-2xx, and there's no reason to retry an event this handler
    was never going to act on.
    """
    raw_body = await request.body()

    try:
        event = verify_webhook_signature(payload=raw_body, sig_header=stripe_signature)
    except Exception as exc:
        log.warning("stripe_webhook.signature_rejected", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature.",
        ) from exc

    handler = _HANDLERS.get(event["type"])
    if handler is None:
        log.info("stripe_webhook.ignored_event_type", event_type=event["type"])
        return {"status": "ignored"}

    try:
        await handler(event["data"])
    except Exception as exc:
        log.exception("stripe_webhook.handler_failed", event_type=event["type"])
        # Let Stripe retry -- a transient DB error shouldn't silently drop a
        # subscription-state update.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Handler failed."
        ) from exc

    return {"status": "processed"}
