"""Kill-switch Cloud Function.

Triggered by a Pub/Sub budget alert. When the alert threshold reaches or
exceeds 100% of the monthly budget, this function disables billing on the
GCP project, which stops all paid services immediately.

DRY_RUN=true  → logs intent only (used during initial verification test).
DRY_RUN=false → actually calls updateBillingInfo with empty account (production).
"""
from __future__ import annotations

import base64
import json
import os

from google.cloud import billing_v1

_PROJECT_ID = os.environ["GCP_PROJECT_ID"]
_DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"

# Threshold at which billing is disabled (1.0 = 100% of budget spent).
_KILL_THRESHOLD = 1.0


def kill_billing(event: dict, context: object) -> None:  # noqa: ARG001
    """Background Cloud Function — Pub/Sub trigger.

    Args:
        event: Pub/Sub message dict with base64-encoded 'data' field.
        context: Cloud Functions event metadata (unused).
    """
    raw_data = base64.b64decode(event["data"]).decode("utf-8")
    payload: dict = json.loads(raw_data)

    cost_amount = float(payload.get("costAmount", 0))
    budget_amount = float(payload.get("budgetAmount", 0))
    threshold = float(payload.get("alertThresholdExceeded", 0))
    currency = payload.get("currencyCode", "USD")
    budget_name = payload.get("budgetDisplayName", "unknown")

    print(
        f"[kill-switch] alert received — budget='{budget_name}' "
        f"cost={cost_amount:.2f} {currency} / {budget_amount:.2f} {currency} "
        f"threshold={threshold:.1%}"
    )

    if threshold < _KILL_THRESHOLD:
        print(
            f"[kill-switch] threshold {threshold:.1%} < {_KILL_THRESHOLD:.0%} "
            f"— no action taken"
        )
        return

    if _DRY_RUN:
        print(
            f"[kill-switch] DRY RUN — threshold {threshold:.1%} ≥ {_KILL_THRESHOLD:.0%} "
            f"— WOULD disable billing on project '{_PROJECT_ID}'"
        )
        print(
            "[kill-switch] DRY RUN — skipping: "
            f"billing_v1.CloudBillingClient().update_project_billing_info("
            f"name='projects/{_PROJECT_ID}', billing_account_name='')"
        )
        return

    print(
        f"[kill-switch] TRIGGERED — threshold {threshold:.1%} ≥ {_KILL_THRESHOLD:.0%} "
        f"— disabling billing on project '{_PROJECT_ID}'"
    )
    client = billing_v1.CloudBillingClient()
    project_name = f"projects/{_PROJECT_ID}"
    empty_billing = billing_v1.ProjectBillingInfo(billing_account_name="")
    result = client.update_project_billing_info(
        name=project_name, project_billing_info=empty_billing
    )
    print(f"[kill-switch] billing disabled — response: {result}")
