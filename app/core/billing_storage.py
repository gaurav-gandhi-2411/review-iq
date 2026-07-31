"""DB sync for Stripe subscription state (Wave 2 P4, ADR 0008).

Kept separate from storage_pg.py -- this is billing-specific, not general
extraction/review storage, and the S0 remediation pass's principle applies
here too: every write is _set_tenant()-scoped to the org_id already resolved
from a verified source (Stripe webhook signature -- see
app/api/webhooks/stripe.py), never an unscoped connection.
"""

from __future__ import annotations

from datetime import datetime

import psycopg2
import structlog

from app.core.billing import PLAN_QUOTAS
from app.core.config import get_settings
from app.core.storage_pg import _db_connect, _set_tenant

log = structlog.get_logger(__name__)


def get_org_id_for_stripe_customer_pg(stripe_customer_id: str) -> str | None:
    """Resolve org_id from a Stripe customer ID -- used when a webhook event
    (e.g. invoice.payment_failed) carries a customer ID but not org_id metadata
    directly. Reads via the unique index on stripe_customer_id
    (20260731000003_billing_subscription_state.sql).

    This one lookup is necessarily unscoped (we don't know org_id yet -- that's
    the whole question) -- mirrors the same narrow, single-purpose pattern the
    S0 remediation used for webhook org-resolution (a SECURITY DEFINER function
    there; a plain indexed lookup here is equally narrow since this table has
    no per-tenant secret in the queried columns, just an ID mapping, and the
    caller is always a signature-verified Stripe webhook, never a public
    request).
    """
    settings = get_settings()
    conn = psycopg2.connect(settings.supabase_database_url)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM public.organizations WHERE stripe_customer_id = %s",
            (stripe_customer_id,),
        )
        row = cur.fetchone()
        return str(row[0]) if row else None
    finally:
        conn.close()


def sync_subscription_pg(
    *,
    org_id: str,
    stripe_customer_id: str,
    stripe_subscription_id: str | None,
    plan: str,
    subscription_status: str | None,
    current_period_end: datetime | None,
) -> None:
    """Update organizations' Stripe/plan state AND the org's active api_keys.quota
    to match -- quota enforcement (app/auth/api_key.py) reads api_keys.quota, not
    organizations.plan, so both must be updated together or a paid customer would
    still be rate-limited at their old (likely free-tier) quota.
    """
    conn = _db_connect()
    conn.autocommit = False
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            """
            UPDATE public.organizations
            SET plan = %s,
                stripe_customer_id = %s,
                stripe_subscription_id = %s,
                subscription_status = %s,
                current_period_end = %s
            WHERE id = %s
            """,
            (
                plan,
                stripe_customer_id,
                stripe_subscription_id,
                subscription_status,
                current_period_end,
                org_id,
            ),
        )
        new_quota = PLAN_QUOTAS.get(plan)
        if new_quota is not None:
            cur.execute(
                """
                UPDATE public.api_keys
                SET quota = %s
                WHERE org_id = %s AND revoked_at IS NULL
                """,
                (new_quota, org_id),
            )
        conn.commit()
        log.info(
            "billing.subscription_synced",
            org_id=org_id,
            plan=plan,
            subscription_status=subscription_status,
            quota_updated=new_quota is not None,
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def downgrade_to_free_pg(*, org_id: str, stripe_customer_id: str) -> None:
    """Subscription canceled or permanently unpaid -- revert to the free plan
    and its quota. `stripe_customer_id` must be the org's EXISTING customer ID
    (from the triggering webhook event) -- kept as-is for the customer's Stripe
    history/portal access even after downgrade, never cleared. No data deleted.
    """
    sync_subscription_pg(
        org_id=org_id,
        stripe_customer_id=stripe_customer_id,
        stripe_subscription_id=None,
        plan="free",
        subscription_status="canceled",
        current_period_end=None,
    )
