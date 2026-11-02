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

## 6. Re-decide the failover (GG decides; CC measures)

The failover key (`groq-api-key`, `openai/gpt-oss-120b`, ADR 0038) lives in the SHARED organisation, not the dedicated one this
runbook upgrades. Other projects (triage-iq's cassette recorder, benchmarks) fill that pool, so the failover can serve only what they
leave. Paying the dedicated organisation does not change this.

What is known (S32, 2026-10-29, corrected; detail in `docs/STATUS.md` S32):

- Ledgered 120b spend (with the probe) peaked at 160,401 tokens in a rolling 24 h (10-29 07:40-08:00 UTC), so the failover had AT MOST 39,599 tokens
  (18 extractions of 2,169) at that hour. The true headroom is unknown: projects without the ledger spend from the same pool, and the
  40,000 production reserve binds only ledger-wired projects. Whether it fell below one extraction is not established.
- Not wired to the ledger and calling gpt-oss-120b: the gg-portfolio chatbot (live), DealHunter's demo profile, swasarthi's evals
  (arithmetic says a different org), agentgauge-r1, token-efficiency-scorer. Which org each key is in is visible only in the Groq console.
- 0 production 429s or failovers 10-22 to 10-29; nightly probe 7 of 7.
- `scripts/failover_headroom.py` gives only the ledgered lower bound on usage. Groq's `x-ratelimit-remaining-tokens` header is
  per-minute, not daily, so it cannot fill the gap.

Before deciding, measure the real number (GG, console): the shared organisation's daily gpt-oss-120b usage for the last 7 days
(console usage page), and list which keys/projects it holds. Then pick one, with that number and the customer's expected daily reviews:

1. **Paid primary limits.** After step 1 the dedicated organisation's own higher limits (step 3) may make a quota-driven failover
   rare; the failover then covers only provider outages, and the shared pool's headroom matters less.
2. **A separate failover.** A failover key in an organisation no other project uses (or another provider), so its headroom is
   the whole pool. Needs a new key in Secret Manager and a production config change (GG approves).
3. **Accept the risk.** Keep the shared failover and record the headroom figure and its date as the accepted risk in ADR 0038.

## 7. Per-project caps on the shared ledger (first sale; GG approves, a triage-iq session implements)

Not adopted before the first sale (GG, S33). The proposal is `docs/plans/groq-ledger-per-project-caps.md` (#633). Why it matters at the first
sale: if option 2 above is not taken, production's failover keeps sharing the pool with the ledger-wired projects. Today the recorder has no
cap, and its recording-active flag makes every other project wait. On 2026-10-30 the recorder held 156,811 of the 160,000 allocatable
gpt-oss-120b tokens, leaving the failover at most the 40,000 reserve minus every non-ledger caller's spend.

1. Re-read `triage-iq/tools/groq_budget/groq_budget.py` (`BUILTIN_CONFIG`, `check`) and `~/.groq_budget/config.json`, then update the
   proposal's "what is true now" section.
2. Adopt the caps per model per trailing 24 h: production reserve 40,000; unallocated slack 40,000 for non-ledger callers;
   `triageiq-recorder` 60,000; `benchmark` 60,000; a 20,000 default for any other project. If the recorder needs more, the proposal's
   variant is 100,000 recorder, 20,000 benchmark and 40,000 slack. Either way the recording flag stops being exclusive beyond the
   recorder's own cap. The change goes in triage-iq, with the backward-compatibility tests listed in the proposal.
3. Record the adopted caps and the date in ADR 0038 next to the failover decision from section 6.

These caps bind only ledger-wired projects. Only option 2 in section 6 (a failover pool no other project uses) protects the failover from
callers that ignore the ledger.

## Rollback

Console: downgrade to Free (step 1.3); remove the spend limit if it blocks access. App: set `ENABLE_COST_ALERTS=false`; the endpoint then
answers `{"ok": true, "enabled": false}`.
