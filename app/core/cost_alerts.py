"""Per-customer inference cost metering and the "cost exceeds 20 percent of the subscription price" alert (S29, flag OFF).

Design (docs/architecture/adr/0041-paid-groq-from-first-payment.md): from a customer's first payment their inference runs on paid
Groq, funded by the subscription. The measured cost of a customer at full quota is 9.2 percent of the Starter price ($2.67 of
$29) but 25.6 percent of India Growth ($13.35 of INR 4,999) and 26.8 percent of Scale, so at FULL quota the larger plans sit above the
20 percent line by design of the prices; at the typical 40 percent utilisation (docs/pricing.json) they sit below it. A month above the
line is a prompt to look (a runaway queue, a prompt regression, a customer far over quota), not proof of a fault.

What this does NOT do: it does not know who is paying. There is no subscription or plan data in the app yet (payments are not built),
so the plan of each paying organisation comes from the `ORG_PLANS_JSON` setting ({"<org uuid>": "starter"}) that GG sets at first sale.
Only the organisations in that map are checked, one tenant-scoped query each (RLS applies; no cross-org query, no migration).
The cost is the sum of `extraction_costs.cost_usd` for the calendar month (UTC), i.e. review extraction only; reply drafting and the
encoder service are not in it (the encoder is a few cents a month in total).

Signals: a structured `cost_alert.threshold_exceeded` log at ERROR (alert on it in Cloud Logging) and the Prometheus
`review_iq_cost_alert_total{plan}` counter. No email is sent from here.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

import structlog

from app.core.metrics import COST_ALERT_TOTAL, ORG_COST_RATIO

log = structlog.get_logger(__name__)

# Monthly price in USD per plan id. Source of truth: docs/pricing.json (tiers' usd; the `_in` ids are the INR price at fx_inr_per_usd,
# rounded to cents). scripts/check_pricing_consistency.py fails CI if this table drifts from the JSON. Agency is custom-quoted and has
# no entry: an org on an unknown plan is skipped loudly. Change a price in the JSON first, never here.
PLAN_PRICE_USD: Final[dict[str, float]] = {
    "starter": 29.0,
    "growth": 79.0,
    "scale": 199.0,
    "starter_in": 15.66,
    "growth_in": 52.24,
    "scale_in": 135.84,
}
DEFAULT_FRACTION: Final = 0.20  # GG decision, S29


@dataclass(frozen=True)
class CostAlert:
    org_id: str
    plan: str
    cost_usd: float
    price_usd: float
    ratio: float


def month_start_utc(now: datetime) -> datetime:
    n = now.astimezone(UTC)
    return n.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def parse_org_plans(raw: str) -> dict[str, str]:
    """`ORG_PLANS_JSON` -> {org_id: plan}. Empty means no paying customers yet. Malformed JSON raises: a broken alert config must be loud."""
    if not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise ValueError("ORG_PLANS_JSON must be a JSON object of org id -> plan id")
    return {k: v.lower() for k, v in data.items()}


def evaluate(
    cost_by_org: Mapping[str, float],
    org_plans: Mapping[str, str],
    fraction: float = DEFAULT_FRACTION,
) -> list[CostAlert]:
    """Orgs whose month-to-date cost is STRICTLY above `fraction` of their plan price. Unknown plans are skipped (and logged)."""
    out: list[CostAlert] = []
    for org_id, cost in cost_by_org.items():
        plan = org_plans.get(org_id, "")
        price = PLAN_PRICE_USD.get(plan)
        if price is None:
            log.error("cost_alert.unknown_plan", org_id=org_id, plan=plan)
            continue
        ratio = cost / price
        if ratio > fraction:
            out.append(CostAlert(org_id, plan, round(cost, 4), price, round(ratio, 4)))
    return sorted(out, key=lambda a: -a.ratio)


def run_sweep(
    org_plans: Mapping[str, str],
    cost_fn: Callable[[str, datetime], float],
    now: datetime | None = None,
    fraction: float = DEFAULT_FRACTION,
) -> dict[str, object]:
    """Meter every configured org for the month so far and raise the alerts. One org's failure never hides the others."""
    since = month_start_utc(now or datetime.now(UTC))
    costs: dict[str, float] = {}
    failed: list[str] = []
    for org_id in org_plans:
        try:
            costs[org_id] = float(cost_fn(org_id, since))
        except Exception:
            log.error("cost_alert.org_failed", org_id=org_id, exc_info=True)
            failed.append(org_id)
    worst: dict[
        str, float
    ] = {}  # per plan: the highest cost / price among its orgs (no org label: bounded cardinality)
    for org_id, cost in costs.items():
        price = PLAN_PRICE_USD.get(org_plans[org_id])
        if price:
            worst[org_plans[org_id]] = max(worst.get(org_plans[org_id], 0.0), cost / price)
    for plan, ratio in worst.items():
        ORG_COST_RATIO.labels(plan=plan).set(ratio)
    alerts = evaluate(costs, org_plans, fraction)
    for a in alerts:
        COST_ALERT_TOTAL.labels(plan=a.plan).inc()
        log.error(
            "cost_alert.threshold_exceeded",
            org_id=a.org_id,
            plan=a.plan,
            month_cost_usd=a.cost_usd,
            plan_price_usd=a.price_usd,
            ratio=a.ratio,
            threshold=fraction,
        )
    return {
        "month_start": since.isoformat(),
        "orgs_checked": len(costs),
        "orgs_failed": failed,
        "threshold": fraction,
        "alerts": [a.__dict__ for a in alerts],
    }
