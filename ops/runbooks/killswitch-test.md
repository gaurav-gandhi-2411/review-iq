# Kill-Switch Verification Runbook

**Purpose:** Prove the kill switch fires correctly before any paid services are deployed.  
**When to run:** Once after initial deployment; re-run after any changes to `ops/budget-killswitch/`.  
**Owner:** gaurav.gandhi2411@gmail.com  
**Last executed:** 2026-05-10

---

## Architecture summary

```
GCP Budget alert
      │
      ▼ (threshold ≥ 100%)
Pub/Sub topic: billing-alerts
      │
      ▼
Cloud Function: billing-killswitch
      │
      ├─ DRY_RUN=true  → logs "WOULD disable billing" (test mode)
      └─ DRY_RUN=false → calls updateBillingInfo(billingAccountName="") (production)
```

---

## Pre-flight checklist

- [ ] `gcloud config get-value project` returns `review-iq-prod`
- [ ] `gcloud config get-value account` returns `gaurav.gandhi2411@gmail.com`
- [ ] Terraform state up-to-date (`terraform plan` shows no changes)
- [ ] Function deployed: `gcloud functions describe billing-killswitch --region us-central1`
- [ ] DRY_RUN=true confirmed in function env (see step 2 below)

---

## Step 1 — Confirm DRY_RUN is active

```bash
gcloud functions describe billing-killswitch \
  --region us-central1 \
  --format="value(environmentVariables)"
```

Expected output contains `DRY_RUN=true`.

---

## Step 2 — Publish a simulated breach message

The budget alert Pub/Sub message schema (v1.0):
```json
{
  "budgetId": "test-budget-id",
  "budgetDisplayName": "review-iq-prod Monthly Cap ($10)",
  "alertThresholdExceeded": 1.1,
  "costAmount": 11.0,
  "costIntervalStart": "2026-05-01T00:00:00Z",
  "budgetAmount": 10.0,
  "budgetAmountType": "SPECIFIED_AMOUNT",
  "currencyCode": "USD"
}
```

`alertThresholdExceeded: 1.1` = 110% of budget. This triggers the kill condition.

Publish command:
```bash
gcloud pubsub topics publish billing-alerts \
  --project review-iq-prod \
  --message '{"budgetId":"test-budget-id","budgetDisplayName":"review-iq-prod Monthly Cap ($10)","alertThresholdExceeded":1.1,"costAmount":11.0,"costIntervalStart":"2026-05-01T00:00:00Z","budgetAmount":10.0,"budgetAmountType":"SPECIFIED_AMOUNT","currencyCode":"USD"}'
```

---

## Step 3 — Read the execution log

```bash
gcloud functions logs read billing-killswitch \
  --region us-central1 \
  --limit 20 \
  --format "table(time_utc,severity,log)"
```

### Expected log sequence (DRY_RUN=true)

| Line | Content |
|------|---------|
| 1 | `[kill-switch] alert received — budget='review-iq-prod Monthly Cap ($10)' cost=11.00 USD / 10.00 USD threshold=110.0%` |
| 2 | `[kill-switch] DRY RUN — threshold 110.0% ≥ 100% — WOULD disable billing on project 'review-iq-prod'` |
| 3 | `[kill-switch] DRY RUN — skipping: billing_v1.CloudBillingClient().update_project_billing_info(name='projects/review-iq-prod', billing_account_name='')` |

All three lines present = **PASS**.

---

## Step 4 — Promote to production (DRY_RUN=false)

After user confirms the above log output:

```bash
# In ops/budget-killswitch/ — update the variable and re-apply
terraform apply -var="dry_run=false"
```

Or via gcloud directly:
```bash
gcloud functions deploy billing-killswitch \
  --region us-central1 \
  --update-env-vars DRY_RUN=false
```

Confirm:
```bash
gcloud functions describe billing-killswitch \
  --region us-central1 \
  --format="value(environmentVariables)"
```

Expected: `DRY_RUN=false`.

---

## Step 5 — Verify billing IAM

The kill-switch service account needs `roles/billing.admin` on the billing account.

```bash
gcloud billing accounts get-iam-policy 014DAE-6B3556-077365 \
  --format="table(bindings.role,bindings.members)"
```

Confirm `killswitch-sa@review-iq-prod.iam.gserviceaccount.com` appears under `roles/billing.admin`.

---

## Recovery — re-enable billing after a real kill-switch fire

If billing was actually disabled:

1. Go to: https://console.cloud.google.com/billing/linkedaccount?project=review-iq-prod
2. Click **Link a billing account**
3. Select **My Billing Account** (014DAE-6B3556-077365)
4. Confirm

Services restart automatically once billing is re-linked (Cloud Run cold-starts on next request).

---

## Test execution log (2026-05-10 — initial verification)

Pub/Sub message published at 18:45 UTC with `alertThresholdExceeded: 1.1` (110%, cost ₹880 / budget ₹800).

```
2026-05-10 18:45:14.138  Function execution started
2026-05-10 18:45:14.282  [kill-switch] alert received — budget='review-iq-prod-monthly-cap' cost=880.00 INR / 800.00 INR threshold=110.0%
2026-05-10 18:45:14.282  [kill-switch] DRY RUN — threshold 110.0% ≥ 100% — WOULD disable billing on project 'review-iq-prod'
2026-05-10 18:45:14.282  [kill-switch] DRY RUN — skipping: billing_v1.CloudBillingClient().update_project_billing_info(name='projects/review-iq-prod', billing_account_name='')
2026-05-10 18:45:14.283  Function execution took 145 ms, finished with status: 'ok'
```

Sub-threshold invocations at 18:36 and 18:58 UTC (real budget alert at ₹0 spend):
```
[kill-switch] threshold 0.0% < 100% — no action taken   ← correct, no kill
```

**Result:** PASS  
**All three expected log lines present:** YES  
**DRY_RUN promoted to false:** PENDING user confirmation  
**Confirmed by:** (awaiting gaurav.gandhi2411@gmail.com)
