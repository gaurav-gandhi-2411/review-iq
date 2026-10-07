# Cost model — infrastructure COGS and margin, at 1/5/20 customers

**Status: planning only (Session 12 P4). No paid tier is enabled by this document — see D5.
Every dollar figure below is either directly measured from this project's own data (marked
VERIFIED) or sourced from a provider's pricing page/aggregator (marked BELIEVED, with a
re-verification note) — never a guess presented as a measurement.**

## 1. Measured per-extraction cost (P4a/P4b) — VERIFIED

The single prior cost figure in this repo ($0.00029625, Session 11) came from one real call.
Real cost depends on the review-length distribution across the actual small/large tier-routing
mix, not one sample. Re-measured against all 106 held-out fixtures (the largest real-review
corpus this project has), replayed against committed cassettes — **$0, zero live LLM calls**
(`eval/measure_token_costs.py`, `eval/results/token_cost_measurement_n106.json`).

| Tier | Model | n (of 106) | Mix | Mean tokens in / out / total | Mean cost/extraction |
|---|---|---|---|---|---|
| small | openai/gpt-oss-20b | 43 | 40.6% | 1,751 / 774 / 2,525 | $0.000363 |
| large | openai/gpt-oss-120b | 63 | 59.4% | 1,746 / 647 / 2,394 | $0.000650 |

**Blended cost per extraction, at the ACTUAL observed tier-routing mix: $0.000534 USD
(₹0.05110).** This is the number for per-customer cost projections below — not either tier's
mean in isolation, and not an assumed split (the router escalates to `large` on schema-
validation failure or small-model exhaustion; **59.4% of calls escalate to the expensive
tier**, materially more than a naive "most stay small" assumption would predict).

## 2. Infrastructure fixed costs — mixed VERIFIED/BELIEVED

| Item | Current state | Projected cost once paid tier flips (D5) | Confidence |
|---|---|---|---|
| Groq | Free tier, quota-bounded | $0 marginal cost change — the per-extraction figures above ARE the real Groq API rate (`app/core/pricing.py`, fetched live 2026-09-10 against console.groq.com/docs/models); moving off free tier changes the *quota ceiling*, not the *per-token price* | VERIFIED (pricing table) |
| Supabase | Free tier | $25/month (Pro) | Given by GG (D2c/P4c) |
| Cloud Run | `maxScale=3`, no `minScale` set — **scale-to-zero today, confirmed live** via `gcloud run services describe` | ~$47.34/month for `min-instances=1` at the current 1 vCPU / 1 GiB allocation (Tier 1 region pricing: $0.000018/vCPU-sec + $0.000002/GiB-sec always-on rates, minus the 240,000 vCPU-sec / 450,000 GiB-sec monthly free allowance) | **BELIEVED** — rates sourced from third-party aggregators summarizing Google's own pricing page (a direct fetch of cloud.google.com/run/pricing returned truncated content); recommend GG cross-check via the Cloud Billing Calculator before this number is used in any external commitment |
| Vercel | Hobby (free) | $20/month (Pro) — **Hobby's ToS prohibits commercial use**, so this is not optional once a customer pays | Given by GG (P4c) |
| Resend | Free (3,000 emails/month, 100/day cap) | $0 while under free-tier volume; $20/month (Pro, 50,000 emails) once exceeded | Pro-tier figure from Resend's public pricing (BELIEVED, third-party-sourced, low stakes at this volume) |
| Payment processing | n/a | ~3% of revenue (given, D2c/P4c) **+ 18% GST charged on that processing fee** (India-specific, given) → effective **3.54% of revenue** | Given by GG |

**Total shared fixed infrastructure cost once the paid tier flips: ~$92.34/month**
(Supabase $25 + Cloud Run $47.34 + Vercel $20 + Resend $0, assuming email volume stays under
the free-tier cap through at least the 20-customer mark — plausible for transactional alert
email at this scale, revisit if digest volume grows).

## 3. Fixed cost per customer, at 1/5/20 customers (P4c)

| Customers | Fixed cost per customer/month |
|---|---|
| 1 | $92.34 |
| 5 | $18.47 |
| 20 | $4.62 |

This is the mechanical reason D3 anchors the margin target at 5 customers, not 1: at 1
customer, fixed costs alone would consume most of a Starter tier's revenue before any variable
LLM cost is even counted. The trajectory also matters — margins **improve** with scale as fixed
cost amortizes further, which is the normal and reassuring shape for this cost structure, not a
model that degrades as it grows.

## 4. D2 pricing checked against real costs at the 5-customer allocation (P4d)

Total cost per tier = (quota × $0.000534/extraction) + $18.47 (fixed, 5-customer allocation) +
3.54% of price (payment processing + GST). Margin = (price − cost) / price.

### International (USD)

| Tier | Quota | Price | Variable cost | Fixed alloc | Payment | Total cost | Margin |
|---|---|---|---|---|---|---|---|
| Starter | 5,000 | $59 | $2.67 | $18.47 | $2.09 | $23.23 | **60.6%** |
| Growth | 25,000 | $99 | $13.35 | $18.47 | $3.50 | $35.32 | **64.3%** |
| Agency | 200,000 | $249 | $106.80 | $18.47 | $8.81 | $134.08 | **46.2%** |

Quotas follow the live pricing page (`site/index.html` lines 763, 771, 785: Starter 5,000,
Growth 25,000, Agency 200,000+ reviews/mo). Starter and Growth now sit above the 45-55% band
(more margin than targeted); Agency sits just above its floor. No correction needed for the
international tiers.

### India (INR)

| Tier | Quota | Price | Variable cost | Fixed alloc | Payment | Total cost | Margin |
|---|---|---|---|---|---|---|---|
| Starter | 5,000 | ₹2,999 ($31.34) | $2.67 | $18.47 | $1.11 | $22.25 | **29.0%** |
| Growth | 25,000 | ₹6,999 ($73.14) | $13.35 | $18.47 | $2.59 | $34.41 | **53.0%** |
| Agency | 200,000 | ₹19,999 ($208.99) | $106.80 | $18.47 | $7.40 | $132.67 | **36.5%** |

**At the 5-customer allocation India Growth clears the 45-55% band; India Starter and Agency do
not, by 16.0 and 8.5 points respectively.** This is not a small rounding gap. The India
tiers are priced at roughly half the USD-equivalent of the international tiers for the *same
quota* (e.g. Starter: $59 international vs. $31.34-equivalent India), while the underlying
Groq/infra costs are identical regardless of the customer's currency — so the India tiers
absorb the full cost base against a smaller revenue number.

**Recommended corrected India prices to clear 45% margin at 5 customers** (solving
`price = (variable + fixed) / (1 − payment_pct − margin_target)`):

| Tier | Current | Price needed for 45% margin | Increase |
|---|---|---|---|
| Starter | ₹2,999 | **₹3,931** | +31% |
| Growth | ₹6,999 | **₹5,917** | none (current price already clears; -15%) |
| Agency | ₹19,999 | **₹23,295** | +16% |

**This is reported, not decided.** D2 explicitly labels this "early access pricing," and
deliberately pricing below the margin target to acquire initial customers is a legitimate,
common go-to-market choice — the gap may be intentional. What this section adds is the exact
size of that gap in real numbers, so it's a stated tradeoff rather than an unmeasured one:
**at 5 India customers on current prices, the India-tier margin runs roughly 29-53% (Growth
inside the band, Starter and Agency below it)** — GG's call on whether "early access" already prices that in, or whether the
correction above should apply now.

## 5. Frontier-model "Precision tier" option (P4e)

Cost per extraction on a frontier model, same blended token profile (1,748 in / 699 out mean,
this session's own measurement) — priced against Claude Opus 4.8 via OpenRouter ($5/M input,
$25/M output — cross-checked against two independent pricing sources after a first WebFetch
pass returned fabricated model names, discarded before use):

**$0.02622/extraction** — roughly **49x** the current Groq blended cost ($0.000534).

At this rate, a Precision tier cannot use the same quota tiers as the Groq-backed product
(25,000 extractions/month would cost **$655.50** in LLM spend alone, before any margin) — it
needs its own, much smaller quota, positioned for customers who want the highest accuracy on a
curated subset of reviews (e.g. only escalated/high-urgency ones), not bulk volume.

Illustrative pricing at 45% margin, 5-customer fixed-cost allocation (GG to choose the actual
quota — these are illustrative anchors, not a recommendation of a specific number):

| Illustrative quota | Variable cost | + fixed alloc | Price needed for 45% margin |
|---|---|---|---|
| 1,000/month | $26.22 | $18.47 | **~$87/month** |
| 5,000/month | $131.08 | $18.47 | **~$290/month** |

## 6. Groq Developer-plan upgrade readiness (Session 13 P3c) — planning only, NOT upgraded

The real free-tier capacity ceiling (~140.6 extractions/day, ~4,217/month combined across every
customer, demo, and eval traffic — see [ADR 0015](architecture/adr/0015-panel-restoration-and-quota-safety-gap.md)'s
Session 13 correction) is smaller than a single Starter-tier customer's monthly allotment
(5,000/month). This makes the Developer-plan upgrade a same-day-as-first-signup necessity, not a
someday optimization — this section exists so that decision doesn't require research at the
moment it's urgent.

**What's VERIFIED**: Groq's free-tier limits for `openai/gpt-oss-20b`/`120b` (30 RPM / 1K RPD /
8K TPM / 200K TPD each), confirmed via a live API call's response headers plus Groq's own
published rate-limits documentation (`console.groq.com/docs/rate-limits`), fetched directly this
session.

**What's NOT independently verified**: the exact Developer-plan numeric limits for these same two
models. Groq's rate-limits page has a Free/Developer tab selector; the Developer tab's table
content did not render through this session's fetch tooling (client-side tab switching, not
captured by a static HTML→Markdown conversion) — confirmed by two separate fetch attempts,
neither returning the Developer table's actual rows. A subsequent web search surfaced a
third-party aggregator claim — "`openai/gpt-oss-120b`: 1K RPM / 500K TPM / 250K TPD on the
Developer plan" — but per this session's own established standard (a prior search result falsely
attributed an xAI-specific header to Groq, caught only by checking Groq's own docs directly), a
blog aggregator's summary is **BELIEVED at best, not VERIFIED**, and is reported here only as a
directional planning input, explicitly flagged as such.

**What GG should verify directly before relying on this for a real upgrade decision** (Console
login required, no credentials available to this session): `console.groq.com/docs/rate-limits`
→ toggle to "Developer Plan" → read the exact RPM/RPD/TPM/TPD for both `gpt-oss` models, and
`console.groq.com/settings/billing` → confirm whether the Developer plan requires a card on file
before any usage, a minimum spend, or is genuinely pay-as-you-go from $0 (Groq's own docs
describe it as "self-serve," which is a good sign but not the same as a confirmed $0-minimum
figure).

**Why this still makes the upgrade a one-step decision even with that gap**: the ACTION itself
(if the BELIEVED Developer-tier numbers are directionally correct) is unambiguous regardless of
the exact final numbers — add a payment method in Groq Console, upgrade the plan, no code change
required (this project's `GROQ_API_KEY` config doesn't change on a plan upgrade, only the limits
attached to it do). The only thing gated on GG's direct verification is *how much better* the new
ceiling is, not *whether* the upgrade path exists or requires engineering work.

## 7. Update (S19 M4a): failover, Secret Manager, and margin at 1/5/10/20 customers

Everything above is unchanged. This section adds the cost lines that did not exist when it was
written and re-runs section 4's margin math at four customer counts. Date: 2026-10-07.

### 7.1 New cost lines

| Item | Figure | Derivation | Confidence |
|---|---|---|---|
| OpenRouter failover call (`meta-llama/llama-3.3-70b-instruct`, DeepInfra, ZDR) | **$0.000196/call** (INR 0.0187) at the probe's 1,612 in / 108 out | 1,612 x $0.10/M + 108 x $0.32/M = $0.0001612 + $0.0000346 | Tokens VERIFIED (failover-probe run in PR #279 commit message, `0a74db8`: "PASS 6767ms ... tokens 1612/108"). Rates BELIEVED: $0.10/M in, $0.32/M out from third-party aggregator summaries (OpenRouter's own page did not return prices through the fetch tool); re-check at openrouter.ai/meta-llama/llama-3.3-70b-instruct |
| Same call on the primary (gpt-oss-120b) | $0.000307 | 1,612 x $0.15/M + 108 x $0.60/M (`app/core/pricing.py`) | VERIFIED (pricing table, as_of 2026-09-10) |
| OpenRouter credit-purchase fee | 5.5%, $0.80 minimum per top-up -> effective ~$0.000207/call with fee; a $5 top-up costs $0.80 (16%) | aggregator summaries | BELIEVED |
| Failover standing cost | ~$0.07/month (one $0.80 top-up per year) while failover is rare | 0.80/12 | BELIEVED |
| Secret Manager | $0.06 per active version per month ($0.000082192/hr x 730), first 6 versions free; $0.03 per 10,000 access ops, first 10,000 free; $0.05 per rotation notification after 3 free | 14 secrets (13 in `ops/runbooks/secret-rotation.md` + `secondary-provider-api-key`, 1 version each) -> (14 - 6) x $0.06 = **$0.48/month**. Access ops: each cold start reads ~12 secrets, so ~800 cold starts/month fit the free 10,000 | Rates BELIEVED (cloud.google.com/secret-manager/pricing returned truncated content; figures from costbench.com quoting the SKU). Secret count VERIFIED from the repo runbook, not from `gcloud` (no live call made) |

Observations that matter more than the cents:

- **The failover path is cheaper per call than the primary** ($0.000196 vs $0.000307 on the same
  tokens), so a long Groq outage does not raise COGS. Its risk is capacity and quality, not cost.
- **The failover model has no entry in `PRICING_TABLE`.** `extract.py` catches `UnknownModelError`,
  logs `extraction.cost_pricing_missing` and skips the cost row, so failover traffic is currently
  invisible in `extraction_costs` COGS. Fix is one table entry; not done here (docs-only PR).
- Per-extraction llama output tokens are unmeasured (the probe prompt is not an extraction). If it
  emits the gpt-oss-120b mean of 647 tokens, an extraction costs ~$0.00038 on failover vs $0.00065
  on the primary tier (BELIEVED).

### 7.2 Shared fixed cost, updated

F = Supabase $25 + Cloud Run $47.34 + Vercel $20 + Resend $0 + Secret Manager $0.48 + failover
$0.07 = **$92.89/month** (was $92.34). Cloud Run is still the min-instances=1 projection, not
today's scale-to-zero. Resend stays $0 only while sends stay under 3,000/month and 100/day.

### 7.3 Margin per tier at 1, 5, 10, 20, 40 customers

Quotas as on the live pricing page (`site/index.html` lines 763, 771, 785): Starter 5,000,
Growth 25,000, Agency 200,000. An earlier revision of this table used 10,000 / 50,000; that was
wrong and is corrected here (S19 N6).

Formulas (all customers assumed on the same tier, 100% quota use, which is the worst case):

- fixed per customer = F / N, F = $92.89 (7.2)
- variable = quota x $0.000534 (blended cost per extraction, section 1)
- payment = 3.54% x price (3% + 18% GST on the fee)
- margin = (price - variable - F/N - payment) / price

| Tier | Price (USD) | Variable | 1 cust | 5 cust | 10 cust | 20 cust | 40 cust |
|---|---|---|---|---|---|---|---|
| Starter intl (5K) | $59 | $2.67 | -65.5% | 60.4% | 76.2% | 84.1% | 88.0% |
| Growth intl (25K) | $99 | $13.35 | -10.9% | 64.2% | 73.6% | 78.3% | 80.6% |
| Agency intl (200K) | $249 | $106.80 | 16.3% | 46.1% | 49.8% | 51.7% | 52.6% |
| Starter IN (5K) | Rs 2,999 = $31.34 | $2.67 | -208.5% | 28.7% | 58.3% | 73.1% | 80.5% |
| Growth IN (25K) | Rs 6,999 = $73.14 | $13.35 | -48.8% | 52.8% | 65.5% | 71.9% | 75.0% |
| Agency IN (200K) | Rs 19,999 = $208.99 | $106.80 | 0.9% | 36.5% | 40.9% | 43.1% | 44.2% |

Worked check (Starter intl, 5): 2.67 + 92.89/5 + 0.0354 x 59 = 2.67 + 18.58 + 2.09 = 23.34;
(59 - 23.34)/59 = 60.4%. Worked check (Growth intl, 40): 13.35 + 92.89/40 + 0.0354 x 99 =
13.35 + 2.32 + 3.50 = 19.17; (99 - 19.17)/99 = 80.6%.
FX: Rs 95.6943/USD (`pricing.py`, 2026-07-31). Prices are treated as GST-exclusive, as before.
Fixed per customer: $92.89 / $18.58 / $9.29 / $4.64 / $2.32.

Resend: the 40-customer column keeps Resend at $0 (3,000 emails/month, 100/day). Whether 40
customers' alert and digest email fits under that cap is unmeasured; if Resend Pro ($20/month)
is needed, F = $112.89 and the 40-customer column becomes: Starter intl 87.2%, Growth intl 80.1%,
Agency intl 52.4%, Starter IN 78.9%, Growth IN 74.3%, Agency IN 44.0%.

Reading: Starter and Growth intl and Growth IN clear 45% at 5 customers; Agency intl clears it
(46.1%) barely. Starter IN clears it at 10 customers (58.3%) but not at 5 (28.7%). Agency IN does
not clear it at any count up to 40 (44.2%). At 1 customer every tier except Agency intl is at or
below break-even, so a single early customer is carried by running paid-tier infrastructure ahead
of revenue.

Note: the live pricing page lists different prices from the ones modelled above ($29 / Rs 1,499
Starter, $79 / Rs 4,999 Growth, a $199 / Rs 12,999 100,000-review Scale tier, Agency "Talk to us";
`site/index.html` lines 764-785), and `docs/payments-readiness.md` records the same figures. This
section's prices ($59 / $99 / $249 and the Rs equivalents) are the D2 planning prices, not the
site's, and are left as they were; only quotas were reconciled in S19 N6. Re-run this table at the
site's prices before quoting a margin externally.

### 7.4 Starter at $15 / Rs 999 at 45% margin (S19 M4b)

Solve 45% for N: price x (1 - 0.0354 - 0.45) - variable = F/N, so N = F / (price x 0.5146 - variable).
Quota is 5,000 (live pricing page). Rs 999 = $10.44 at Rs 95.6943/USD.

| Case | Price | Quota | Room for fixed cost per customer | Customers needed (F = $92.89) | If Resend Pro is needed (F = $112.89) |
|---|---|---|---|---|---|
| Starter at $15 | $15.00 | 5,000 | 15 x 0.5146 - 2.67 = $5.049 | **19** (18.4) | 23 (22.4) |
| Starter at Rs 999 | $10.44 | 5,000 | 10.44 x 0.5146 - 2.67 = $2.702 | **35** (34.4) | 42 (41.8) |
| $15, 10,000 quota (superseded) | $15.00 | 10,000 | $2.379 | 40 (39.05) | 48 (47.45) |
| Rs 999, 10,000 quota (superseded) | $10.44 | 10,000 | $0.032 | ~2,900 (not feasible) | ~3,500 |

Assumptions: every customer is on this tier at full quota use; F as in 7.2; blended
$0.000534/extraction; no discounts, taxes beyond the 3.54%, or support cost; cost per extraction
does not fall with volume; the Groq paid plan is live (see below).

Conclusion: on the 5,000 quota both prices reach 45% margin: **$15 at 19 customers, Rs 999 at 35
customers** (23 and 42 if Resend Pro is needed). The earlier 40-customer / not-feasible result came
from modelling a 10,000 quota that the site does not offer. A cheaper per-extraction cost lowers
the Rs 999 figure further: at 100% small-tier routing (c = $0.000363, variable $1.82) Rs 999 needs
N = 92.89 / (5.372 - 1.82) = 26.
Hard prerequisite: at 35 customers on Starter that is 175,000 extractions/month (19 customers:
95,000) against the ~4,217/month free-tier ceiling (section 6), so the Groq Developer plan must be
live, and its limits are still unverified.

## Provenance

- `eval/measure_token_costs.py` / `eval/results/token_cost_measurement_n106.json` — this
  session, $0, cassette-replay, reproducible.
- `app/core/pricing.py` — Groq rates, verified live 2026-09-10.
- Cloud Run scaling config — verified live via `gcloud run services describe review-iq` (no
  `minScale` annotation present, confirming scale-to-zero today), 2026-09-12.
- Cloud Run always-on pricing rates, OpenRouter frontier-model rates — BELIEVED, third-party
  sourced, flagged individually above with re-verification recommendations.
- Section 7: failover tokens from PR #279 (`0a74db8`); OpenRouter, Secret Manager and Resend rates
  are BELIEVED (third-party summaries, 2026-10-07). Margins computed in-session from the formulas shown.
- Supabase Pro, Vercel Pro, payment-processing %, GST % — given directly by GG this session.
