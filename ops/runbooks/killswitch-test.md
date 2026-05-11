# Kill-Switch Verification Runbook

**Purpose:** Prove the kill switch fires correctly before any paid services are deployed.  
**When to run:** Once after initial deployment; re-run after any changes to `ops/budget-killswitch/`.  
**Owner:** gaurav.gandhi2411@gmail.com  
**Last executed:** 2026-05-10 (full disable path verified)  
**Re-verified:** 2026-05-11 — `terraform plan -detailed-exitcode` exit 0, zero drift, pre-Step-7 Cloud Run deploy

---

## Architecture summary

```
GCP Budget alert (all thresholds → Pub/Sub)
      │
      ▼
Pub/Sub topic: billing-alerts  (projects/review-iq-prod/topics/billing-alerts)
      │
      ▼ (invoked for EVERY threshold crossing)
Cloud Function: billing-killswitch  (us-central1, Python 3.11, 128MB)
      │
      ├─ threshold < 100%  → log "no action taken", return
      └─ threshold ≥ 100%  → call updateBillingInfo(billingAccountName="")
                              → billing disabled → all paid services stop
```

### Budget thresholds and their effects

Budget cap: **₹100/mo** (≈$1.20 USD; tight cap chosen because GCP billing latency is ~24h)

| Threshold | Amount | Email to billing admin? | Pub/Sub fired? | Kill switch acts? |
|-----------|--------|-------------------------|----------------|-------------------|
| 5%        | ₹5     | YES (default IAM recipients) | YES | NO — logged, no action |
| 10%       | ₹10    | YES | YES | NO — logged, no action |
| 50%       | ₹50    | YES | YES | NO — logged, no action |
| 100%      | ₹100   | YES | YES | **YES — billing disabled** |

Sub-threshold invocations: the function receives every Pub/Sub message but only acts at `alertThresholdExceeded >= 1.0`. Sub-threshold logs read: `[kill-switch] threshold X.X% < 100% — no action taken`.

Email delivery: controlled by `disable_default_iam_recipients = false` in the budget's `all_updates_rule`. GCP automatically emails all billing account IAM recipients (incl. gaurav.gandhi2411@gmail.com) at every threshold crossing. This is separate from and independent of the Pub/Sub + function path.

---

## Pre-flight checklist

- [ ] `gcloud config get-value project` returns `review-iq-prod`
- [ ] `gcloud config get-value account` returns `gaurav.gandhi2411@gmail.com`
- [ ] `terraform plan` shows no changes
- [ ] Function env: `gcloud functions describe billing-killswitch --region us-central1 --format="value(environmentVariables)"` shows `DRY_RUN=false`

---

## How to run the test

### Step 1 — Confirm DRY_RUN status

```bash
gcloud functions describe billing-killswitch \
  --region us-central1 \
  --format="value(environmentVariables)"
```

Production state: `DRY_RUN=false;GCP_PROJECT_ID=review-iq-prod`

---

### Step 2 — Test sub-threshold (no kill)

Publish a 50% alert. Function should log and return without disabling billing.

```bash
gcloud pubsub topics publish billing-alerts \
  --project review-iq-prod \
  --message '{"budgetId":"ae30ad27-4f8b-4d1f-9ac6-4439d984e727","budgetDisplayName":"review-iq-prod-monthly-cap","alertThresholdExceeded":0.5,"costAmount":50.0,"costIntervalStart":"2026-05-01T00:00:00Z","budgetAmount":100.0,"budgetAmountType":"SPECIFIED_AMOUNT","currencyCode":"INR"}'
```

Expected log (within ~30s):
```
[kill-switch] alert received — ... threshold=50.0%
[kill-switch] threshold 50.0% < 100% — no action taken
Function execution took ~111 ms, finished with status: 'ok'
```

Verify billing still enabled:
```bash
gcloud billing projects describe review-iq-prod
# billingEnabled: true  ← must be true
```

---

### Step 3 — Test real breach (billing disable)

**WARNING: this disables billing on the project. All paid services stop immediately. Re-enable manually after verification (step 4).**

```bash
gcloud pubsub topics publish billing-alerts \
  --project review-iq-prod \
  --message '{"budgetId":"ae30ad27-4f8b-4d1f-9ac6-4439d984e727","budgetDisplayName":"review-iq-prod-monthly-cap","alertThresholdExceeded":1.1,"costAmount":110.0,"costIntervalStart":"2026-05-01T00:00:00Z","budgetAmount":100.0,"budgetAmountType":"SPECIFIED_AMOUNT","currencyCode":"INR"}'
```

Expected log sequence (within ~30s):
```
[kill-switch] alert received — ... cost=110.00 INR / 100.00 INR threshold=110.0%
[kill-switch] TRIGGERED — threshold 110.0% ≥ 100% — disabling billing on project 'review-iq-prod'
[kill-switch] billing disabled — response: name: "projects/review-iq-prod/billingInfo" project_id: "review-iq-prod"
Function execution took ~2610 ms, finished with status: 'ok'
```

Verify billing disabled:
```bash
gcloud billing projects describe review-iq-prod
# billingEnabled: false  ← kill switch worked
```

Read logs:
```bash
gcloud functions logs read billing-killswitch \
  --region us-central1 \
  --limit 10 \
  --format "table(time_utc,severity,log)"
```

---

### Step 4 — Re-enable billing

```bash
gcloud billing projects link review-iq-prod \
  --billing-account=014DAE-6B3556-077365
```

Expected output:
```
billingAccountName: billingAccounts/014DAE-6B3556-077365
billingEnabled: true
```

---

### Step 5 — Verify project restored

```bash
# Pub/Sub topic intact
gcloud pubsub topics list --project review-iq-prod

# Function still ACTIVE with DRY_RUN=false
gcloud functions describe billing-killswitch --region us-central1 \
  --format="value(status,environmentVariables)"

# Terraform state clean
cd ops/budget-killswitch && terraform plan  # must show "No changes"
```

---

## Recovery — re-enable billing after a real kill-switch fire

Identical to Step 4 above:

```bash
gcloud billing projects link review-iq-prod \
  --billing-account=014DAE-6B3556-077365
```

Then verify:
```bash
gcloud billing projects describe review-iq-prod
# billingEnabled: true
```

Services restart automatically once billing is re-linked (Cloud Run cold-starts on next request).

---

## Canonical test record — 2026-05-10

**Operator:** gaurav.gandhi2411@gmail.com  
**Function config at test time:** `DRY_RUN=false`, budget cap ₹100 INR

### Phase 1 — DRY_RUN path (18:45 UTC)

Initial test with `DRY_RUN=true` to verify function routing before real billing impact.

Message: `alertThresholdExceeded: 1.1`, cost ₹880 / budget ₹800 (pre-cap-reduction)

```
2026-05-10 18:45:14.138  Function execution started
2026-05-10 18:45:14.282  [kill-switch] alert received — budget='review-iq-prod-monthly-cap' cost=880.00 INR / 800.00 INR threshold=110.0%
2026-05-10 18:45:14.282  [kill-switch] DRY RUN — threshold 110.0% ≥ 100% — WOULD disable billing on project 'review-iq-prod'
2026-05-10 18:45:14.282  [kill-switch] DRY RUN — skipping: billing_v1.CloudBillingClient().update_project_billing_info(name='projects/review-iq-prod', billing_account_name='')
2026-05-10 18:45:14.283  Function execution took 145 ms, finished with status: 'ok'
```

Result: **PASS** — DRY_RUN path confirmed.

---

### Phase 2 — Sub-threshold test (19:55:11 UTC)

After promoting to `DRY_RUN=false` and lowering cap to ₹100.

Message: `alertThresholdExceeded: 0.5`, cost ₹50 / budget ₹100

```
2026-05-10 19:55:11.719  Function execution started
2026-05-10 19:55:11.826  [kill-switch] alert received — budget='review-iq-prod-monthly-cap' cost=50.00 INR / 100.00 INR threshold=50.0%
2026-05-10 19:55:11.827  [kill-switch] threshold 50.0% < 100% — no action taken
2026-05-10 19:55:11.831  Function execution took 111 ms, finished with status: 'ok'
```

`gcloud billing projects describe review-iq-prod` → `billingEnabled: true` ← confirmed no kill.

Result: **PASS** — sub-threshold correctly ignored.

---

### Phase 3 — Real breach (19:55:18 UTC)

Message: `alertThresholdExceeded: 1.1`, cost ₹110 / budget ₹100

```
2026-05-10 19:55:18.125  Function execution started
2026-05-10 19:55:18.129  [kill-switch] alert received — budget='review-iq-prod-monthly-cap' cost=110.00 INR / 100.00 INR threshold=110.0%
2026-05-10 19:55:18.129  [kill-switch] TRIGGERED — threshold 110.0% ≥ 100% — disabling billing on project 'review-iq-prod'
2026-05-10 19:55:20.734  [kill-switch] billing disabled — response: name: "projects/review-iq-prod/billingInfo" project_id: "review-iq-prod"
2026-05-10 19:55:20.736  Function execution took 2610 ms, finished with status: 'ok'
```

`gcloud billing projects describe review-iq-prod` → `billingEnabled: false` ← confirmed disabled.

Result: **PASS** — billing disabled in 2.6 seconds.

---

### Phase 4 — Re-enable and restore (19:57 UTC)

Verified in GCP Console by gaurav.gandhi2411@gmail.com. Re-enabled:

```bash
gcloud billing projects link review-iq-prod --billing-account=014DAE-6B3556-077365
# billingEnabled: true
```

Post-restore verification:
- Pub/Sub topic `billing-alerts`: **present**
- Function `billing-killswitch`: **ACTIVE**, `DRY_RUN=false`
- `terraform plan`: **No changes** (zero drift)

Result: **PASS** — project fully restored.

---

**Overall result: ALL PHASES PASS**  
**Kill switch verified by:** gaurav.gandhi2411@gmail.com, 2026-05-10  
**GCP cost incurred during test: ₹0.00**
