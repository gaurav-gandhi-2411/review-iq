# ADR 0041: Free tiers until the first paying customer; from their first payment, their inference runs on paid Groq

Status: accepted 2026-10-20 (GG decision, S29). Builds on ADR 0037 (Groq capacity is per organisation) and
`docs/cost-model.md`. Nothing is activated by this ADR: no card is added, no flag is turned on.

## Context

- Free inference cannot serve the product. Measured in S28 (`docs/STATUS.md`, "Capacity is product-wide"): the whole business can
  extract about 4,217 reviews a month on free Groq (0.84 of one Starter customer at its 5,000-review quota); with the encoder-first
  router (not enabled) about 12,143, roughly two Starter customers. One Growth customer (25,000) needs 5.9 times today's limit.
- What a customer costs on paid Groq, at the tokens measured over 106 fixtures (1,751 in / 774 out on gpt-oss-20b, 1,746 in / 647 out
  on gpt-oss-120b, blended $0.000534 per review; `eval/results/capacity_model.json`):

| Plan (USD / INR price from `docs/pricing.json`) | Reviews / month | Groq cost at full quota | Share of price (USD / India) | With router R1 (open rate 0.1) |
|---|---|---|---|---|
| Starter ($29 / INR 1,499 = $15.66) | 5,000 | $2.67 | 9.2 % / 17.0 % | $0.93 (3.2 % / 5.9 %) |
| Growth ($79 / INR 4,999 = $52.24) | 25,000 | $13.35 | 16.9 % / 25.6 % | $4.64 (5.9 % / 8.9 %) |
| Scale ($199 / INR 12,999 = $135.84) | 100,000 | $53.40 | 26.8 % / 39.3 % | $18.56 (9.3 % / 13.7 %) |

  At FULL quota Growth India, Scale and Scale India are above the 20 percent alert line below, and Starter India is just under it; at the
  typical 40 percent utilisation `docs/pricing.json` assumes, every plan is below it (the highest is Scale India at 15.7 %). So the alert
  is a prompt to look, not proof of a fault, on the larger plans. The USD and INR prices and the 40 percent figure come from
  `docs/pricing.json`; the older table in `docs/cost-model.md` section 4 ($59 / $99, INR 2,999 / 6,999) is a superseded proposal, and
  the S28 STATUS capacity text that quoted it (Starter $59, Growth $99) is corrected in the S29 STATUS update. The cost column does not
  depend on price. The pricing site and prices are not changed by this ADR.
- Groq's own pages (read 2026-10-20, VERIFIED): token prices gpt-oss-20b $0.075 in / $0.30 out and gpt-oss-120b $0.15 / $0.60 per
  million tokens (the same as `app/core/pricing.py`); a cached-input price is not listed. The Developer plan needs a payment method
  (card, US bank or SEPA debit), applies immediately with no immediate charge, and bills monthly in arrears with extra charges when
  cumulative usage reaches $1, $10, $100, $500 and $1,000 (for an India billing address: $1, $10, then every $100). A hard monthly
  spend limit can be set under Settings, Billing, Limits (paid accounts, owner only); at the limit EVERY key in the organisation gets
  `400 blocked_api_access`; spend is tracked with a 10 to 15 minute delay; only monthly limits exist; alerts can be added.
- **NOT verified: the Developer plan's rate limits.** The public rate-limit page shows one per-model table (gpt-oss-20b and 120b: 30 RPM,
  1K RPD, 8K TPM, 200K TPD) that it calls "the base limits for the Developer plan", but those are the numbers ADR 0037 and the
  cost model took as the FREE limits, and the page does not label the table by plan. If they are also the paid limits, a single Starter
  customer at quota (about 167 reviews a day; with the measured tier mix of 40.6 percent on 20b and 59.4 percent on 120b that is about
  171K tokens a day on 20b and 237K on 120b, BELIEVED arithmetic from `docs/cost-model.md`) would exceed the 200K tokens-per-day pool
  of gpt-oss-120b. The real numbers are on the account's own limits page after the upgrade; the runbook makes reading them a step
  with a decision rule.

## Decision

1. Until the first paying customer, everything stays on free tiers. No card is added, no spend exists.
2. From the first payment, that customer's inference runs on paid Groq (Developer plan on the dedicated Samidha organisation of
   ADR 0037, not the account shared with other products), funded by the subscription. The measured cost at full quota is 9.2 to 39.3
   percent of the price depending on plan and currency (table above); the margins in `docs/cost-model.md` use the same $0.000534 per
   review.
3. A hard monthly spend limit is set on that organisation at the upgrade, with alerts at 50, 75 and 90 percent. It is sized well above
   the expected bill (see the runbook) because reaching it stops all extraction in the organisation.
4. Per-customer cost is metered (`extraction_costs`, already recorded per extraction) and an alert fires when a paying customer's
   month-to-date inference cost exceeds 20 percent of their plan price (`app/core/cost_alerts.py`, flag `ENABLE_COST_ALERTS`, OFF).
   The plan of each paying organisation comes from a setting at first sale because the app holds no subscription data yet.
5. Playbook for the first sale: `ops/runbooks/groq-paid-tier.md`.
6. The encoder-first router stays a later cost lever, not a precondition: it needs a persistence migration, an on-open extraction
   path and a numerics decision first (`docs/STATUS.md`).

## Consequences

- Revenue starts before the capacity risk does: the first customer is deliverable. A second customer on free tiers is not.
- A spend limit is a production kill switch for the whole organisation. Too low is an outage; none is an unbounded bill. The 20
  percent alert and the 90 percent usage alert are the early warnings; they are not rate limits.
- Paid does not mean unlimited: the paid per-model limits are unverified and must be read and written down at the upgrade. A shortfall
  has two answers (ask Groq for higher limits; shed work to the failover path), neither is free of cost or risk.
- The alert covers review extraction only (reply drafting and the encoder service are not in it) and needs `ORG_PLANS_JSON` to
  know who pays. Forgetting to set it leaves paying customers unmonitored; the runbook lists it as a step.
- Progressive billing means a charge appears at $1 and $10 of usage, early in the first month, not at the invoice date.

## Alternatives considered

- Stay on free tiers: cannot deliver two Starter customers, let alone Growth (numbers above).
- Pay for everything from day one: spends before any revenue; rejected by GG.
- Pass inference through to the customer (usage-based price): changes the public pricing, which is GG's decision, not taken here.
- Another paid provider as primary: the cost model is built on the measured Groq tokens and prices; a switch would need a new
  measurement and is not justified by anything seen so far.
