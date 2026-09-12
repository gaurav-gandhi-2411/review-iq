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
| Starter | 10,000 | $59 | $5.34 | $18.47 | $2.09 | $25.90 | **56.1%** |
| Growth | 50,000 | $99 | $26.70 | $18.47 | $3.51 | $48.68 | **50.8%** |
| Agency | 200,000 | $249 | $106.80 | $18.47 | $8.82 | $134.09 | **46.1%** |

**All three international tiers land inside or very close to the 45-55% band as-is.** No
correction needed.

### India (INR)

| Tier | Quota | Price | Variable cost | Fixed alloc | Payment | Total cost | Margin |
|---|---|---|---|---|---|---|---|
| Starter | 10,000 | ₹2,999 ($31.34) | $5.34 | $18.47 | $1.11 | $24.92 | **20.5%** |
| Growth | 50,000 | ₹6,999 ($73.14) | $26.70 | $18.47 | $2.59 | $47.76 | **34.7%** |
| Agency | 200,000 | ₹19,999 ($208.98) | $106.80 | $18.47 | $7.40 | $132.67 | **36.5%** |

**None of the three India tiers clear the 45-55% band at the 5-customer allocation — all three
are materially below it, by 8.5–24.5 points.** This is not a small rounding gap. The India
tiers are priced at roughly half the USD-equivalent of the international tiers for the *same
quota* (e.g. Starter: $59 international vs. $31.34-equivalent India), while the underlying
Groq/infra costs are identical regardless of the customer's currency — so the India tiers
absorb the full cost base against a smaller revenue number.

**Recommended corrected India prices to clear 45% margin at 5 customers** (solving
`price = (variable + fixed) / (1 − payment_pct − margin_target)`):

| Tier | Current | Price needed for 45% margin | Increase |
|---|---|---|---|
| Starter | ₹2,999 | **₹4,414** | +47% |
| Growth | ₹6,999 | **₹8,387** | +20% |
| Agency | ₹19,999 | **₹23,283** | +16% |

**This is reported, not decided.** D2 explicitly labels this "early access pricing," and
deliberately pricing below the margin target to acquire initial customers is a legitimate,
common go-to-market choice — the gap may be intentional. What this section adds is the exact
size of that gap in real numbers, so it's a stated tradeoff rather than an unmeasured one:
**at 5 India customers on current prices, the blended India-tier margin runs roughly 20-37%,
not 45-55%** — GG's call on whether "early access" already prices that in, or whether the
correction above should apply now.

## 5. Frontier-model "Precision tier" option (P4e)

Cost per extraction on a frontier model, same blended token profile (1,748 in / 699 out mean,
this session's own measurement) — priced against Claude Opus 4.8 via OpenRouter ($5/M input,
$25/M output — cross-checked against two independent pricing sources after a first WebFetch
pass returned fabricated model names, discarded before use):

**$0.02622/extraction** — roughly **49x** the current Groq blended cost ($0.000534).

At this rate, a Precision tier cannot use the same quota tiers as the Groq-backed product
(50,000 extractions/month would cost **$1,310.75** in LLM spend alone, before any margin) — it
needs its own, much smaller quota, positioned for customers who want the highest accuracy on a
curated subset of reviews (e.g. only escalated/high-urgency ones), not bulk volume.

Illustrative pricing at 45% margin, 5-customer fixed-cost allocation (GG to choose the actual
quota — these are illustrative anchors, not a recommendation of a specific number):

| Illustrative quota | Variable cost | + fixed alloc | Price needed for 45% margin |
|---|---|---|---|
| 1,000/month | $26.22 | $18.47 | **~$87/month** |
| 5,000/month | $131.08 | $18.47 | **~$290/month** |

## Provenance

- `eval/measure_token_costs.py` / `eval/results/token_cost_measurement_n106.json` — this
  session, $0, cassette-replay, reproducible.
- `app/core/pricing.py` — Groq rates, verified live 2026-09-10.
- Cloud Run scaling config — verified live via `gcloud run services describe review-iq` (no
  `minScale` annotation present, confirming scale-to-zero today), 2026-09-12.
- Cloud Run always-on pricing rates, OpenRouter frontier-model rates — BELIEVED, third-party
  sourced, flagged individually above with re-verification recommendations.
- Supabase Pro, Vercel Pro, payment-processing %, GST % — given directly by GG this session.
