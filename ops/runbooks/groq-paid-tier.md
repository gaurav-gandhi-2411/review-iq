# Runbook: move Samidha's Groq organisation to the paid tier at the first sale (ADR 0041)

Do this when the FIRST paying customer's first payment has cleared, not before. Until then nothing here is done: no card, no flag,
no scheduler job. Every step marked GG needs the Groq console login or a production change; CC cannot do those and does not hold a
write-capable production credential in CI.

Facts below are from Groq's own pages read 2026-10-20 (console.groq.com/docs/billing-faqs, /docs/spend-limits, /docs/models,
/docs/rate-limits). Groq changes these pages; re-read them before relying on a number.

## 0. Before you start (GG)

- Sign in to the Groq console with the account that owns the DEDICATED Samidha organisation (ADR 0037), not the shared account that
  other products use. The organisation's name is in the console's top-left switcher. A limit or a spend cap applies to every key in
  the organisation, so doing this on the wrong one hits the wrong products.
- Know the expected bill: the paying customer's plan from the table in ADR 0041 (Starter about $2.67 a month, Growth about $13.35, Scale about $53.40 at
  full quota; about 35 percent of that with the encoder router, which is not enabled).

## 1. Upgrade to the Developer plan (GG, console)

1. Settings, Billing, Plans (`https://console.groq.com/settings/billing/plans`): choose **Developer**.
2. Add a payment method (credit card; US bank and SEPA debit are also accepted). The upgrade applies immediately and there is no
   immediate charge. Billing is monthly in arrears; Groq also bills early when cumulative usage reaches $1, $10, $100, $500 and
   $1,000 (with an India billing address: $1, $10, then every $100), so the first small charges arrive in the first days. Make sure the card
   has headroom.
3. You can return to Free from Settings, Billing; Groq issues a final invoice that must be paid first. That is the rollback.

## 2. Set a hard monthly spend limit (GG, console)

1. Settings, Billing, **Limits**, Add Limit. Only monthly limits exist; owner permission is required.
2. Amount: at least 5 times the expected monthly bill and never below $25 (a Starter customer is about $3 a month; the cap is a
   backstop against a runaway, not a budget). Reaching it makes EVERY key in the organisation return `400 blocked_api_access`, which
   stops production extraction. Spend is tracked with a 10 to 15 minute delay, so the real total can overshoot slightly.
3. Add usage alerts at 50, 75 and 90 percent of the limit (they go to the billing email addresses; check that mailbox is read).
4. To restore access after a block, raise or remove the limit; it takes effect at once.

## 3. Read the real rate limits and write them down (GG, console; then CC)

1. Settings, Limits (`https://console.groq.com/settings/limits`): note requests and tokens per minute and per day for
   `openai/gpt-oss-20b` and `openai/gpt-oss-120b` on the Developer plan.
2. Record them in the table below and in ADR 0041 (replace the "NOT verified" paragraph).
3. Decision rule: the customer's daily token need is quota / 30 x 2,400 tokens (Starter about 400K a day across both models, Growth
   about 2M). If a per-model daily limit is below that model's share (20b 40 percent, 120b 60 percent of the tokens), do NOT go live
   on the paid org alone: ask Groq for higher limits (their page says they exist for select workloads), and until they are granted
   cap the customer's ingest rate or accept the failover path.

| Model | RPM | RPD | TPM | TPD | Read on |
|---|---|---|---|---|---|
| openai/gpt-oss-20b | ? | ? | ? | ? | (fill in at the upgrade) |
| openai/gpt-oss-120b | ? | ? | ? | ? | (fill in at the upgrade) |

## 4. Turn on per-customer cost metering and the 20 percent alert (CC prepares, GG approves the production change)

1. Pick the secret: create a Secret Manager secret `cost-alert-trigger-token` (random 32+ bytes) and bind it to `review-iq` as
   `COST_ALERT_TRIGGER_TOKEN`. Never paste it in chat.
2. Set on the `review-iq` Cloud Run service: `ENABLE_COST_ALERTS=true`, `COST_ALERT_FRACTION=0.20`,
   `ORG_PLANS_JSON={"<customer org uuid>":"starter"}` (plan ids: `starter`, `growth`, `scale` and the India variants `starter_in`, `growth_in`, `scale_in`; Agency has no price entry and
   is skipped with an error log until one is added). The customer's org uuid is in the `orgs` table.
3. Cloud Scheduler job, daily: `POST https://api.samidhareviews.xyz/internal/cost-alerts/run` with header `X-Cost-Alert-Trigger-Token`.
4. Cloud Logging alert policy on the log event `cost_alert.threshold_exceeded` (severity ERROR) that emails GG. The sweep only logs and
   counts; it sends no email itself.
5. Check: call the endpoint once by hand; expect `{"ok": true, "enabled": true, "orgs_checked": 1, "alerts": []}`.
6. Add the new customer to `ORG_PLANS_JSON` at every sale. A paying customer missing from the map is not monitored.

What it measures: the sum of `extraction_costs.cost_usd` for the calendar month (UTC), review extraction only. Reply drafting and the
encoder service are not in it.

## 5. Keys and CI

The existing production Groq key lives in the dedicated organisation; moving the organisation to the paid plan does not change the
key. No key is created or rotated by these steps. CI never holds a write-capable production credential.

## Rollback

Console: downgrade to Free (step 1.3); remove the spend limit if it blocks access. App: set `ENABLE_COST_ALERTS=false`; the endpoint then
answers `{"ok": true, "enabled": false}`.
