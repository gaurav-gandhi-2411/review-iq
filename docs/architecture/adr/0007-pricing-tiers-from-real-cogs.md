# ADR 0007: Pricing Tiers — Derived from Real Inference Cost, Not Guessed

**Status:** Proposed — a pricing recommendation for GG to review, not an executed decision.
**Date:** 2026-07-31. **Amended 2026-07-31 (Wave 2 close-out P0)**: every cost figure in this
document is an **ESTIMATE**, not a measurement — Section G's cost telemetry has never run
against real traffic (see Context). The original version of this ADR stated a categorical
"usage pricing is coherent" / "inference-dominated" conclusion without modeling fixed
infrastructure floors (Cloud Run minimum instances, the Supabase plan floor, Artifact Registry
storage) or how they change with volume. That was a real omission, not a rounding error — see
the rewritten crossover analysis below. **Nothing in this document should appear on any
customer-facing surface, in a README, or in a pitch deck as a measured number.**
**Scope:** Wave 2 P3. Prices tiers from ESTIMATED inference-cost data; explicitly does not
implement billing (that's P4, ADR 0008).

## Context — what data actually exists

Section G built real per-extraction cost telemetry (`app/core/pricing.py`, real pricing
constants live-verified against each provider's own pricing page 2026-07-31) but **it lives on
unmerged branch `feat/wave1-g-cost-telemetry` and was never deployed** — confirmed via a direct,
read-only query against the live production database: `public.extraction_costs` does not exist.
**Real recorded per-extraction cost sample size is 0 — not thin, nonexistent.** Per the standing
instruction and this project's own established precedent (Section F's SLO report: "n=2 real
extractions in 14 days... honestly reported as INSUFFICIENT DATA rather than fabricated"), this
ADR does **not** extrapolate a per-1k-extraction cost from a handful of production calls. It
uses the real, verified pricing constants against real, verified token counts from actual Groq
responses already on file (the eval cassette store) — an honest **theoretical COGS estimate**,
labeled as such throughout, not a measured production cost.

## Assumption set — stated explicitly, so every figure below can be traced back to it

Every dollar figure in this ADR is an **ESTIMATE** built by chaining these assumptions. Any one
of them changing changes every downstream number:

1. **Token counts stand in for production usage.** The 68 real Groq responses used below are
   from this project's own eval/test corpus, not paying-customer traffic — no such traffic has
   ever been recorded (Section G was never deployed until this close-out pass — see P1). Real
   customer review length/language mix may differ.
2. **Language mix (70% en / 15% hi / 15% hi-en) is illustrative, not measured.** No real
   customer language distribution exists to measure yet.
3. **USD→INR (95.6943) is a point-in-time snapshot** (fetched 2026-07-31), not a live rate.
4. **Provider pricing (Groq per-token, Google Artifact Registry per-GB, Cloud Run per-vCPU/GiB
   -second) is each provider's current published rate**, live-checked on 2026-07-31 — subject to
   change without notice, and Cloud Run's asia-south1 rate specifically is estimated as a ~15%
   regional premium over the cited us-central1 baseline (a documented range, not a verified
   asia-south1-specific figure).
5. **Cloud Run `minInstances=0` (scale-to-zero) is assumed to continue** — verified as the
   *current* live configuration (see below), but a future operational decision to keep an
   instance warm would introduce a large new fixed cost this ADR prices as a sensitivity, not a
   forecast.
6. **DB growth per additional extraction is extrapolated linearly** from the *current* measured
   average row size — real growth is very unlikely to stay perfectly linear (index bloat,
   vacuum behavior, schema changes all affect this), so treat the "extractions until Supabase's
   free tier is exhausted" figure as order-of-magnitude, not exact.

## Real inputs used

**Pricing** (`app/core/pricing.py`, live-verified 2026-07-31 against each provider's own
pricing page — not recalled from memory):

| Model | Tier | USD / 1M input | USD / 1M output |
|---|---|---|---|
| `llama-3.1-8b-instant` | small | $0.05 | $0.08 |
| `llama-3.3-70b-versatile` | large | $0.59 | $0.79 |

USD→INR: 95.6943 (fetched live 2026-07-31 from open.er-api.com — a point-in-time snapshot,
re-fetch before quoting a customer a rupee price).

**Token counts** — real, from actual Groq responses already recorded in
`eval/cassettes/cassettes.json` (68 real cached extraction calls made during eval work), parsed
and bucketed by the `language` field each response itself reports, cross-referenced against this
project's own established tiered-routing rule (en → small tier, hi/hi-en → large tier):

| Language | Tier | n (real calls) | avg tokens in | avg tokens out |
|---|---|---|---|---|
| en | small | 36 | 1693.1 | 139.7 |
| hi | large | 11 | 907.7 | 114.1 |
| hi-en | large | 21 | 1808.8 | 126.0 |

**Caveat, stated plainly**: these are real Groq responses, but from the **eval/test corpus**,
not live paying-customer traffic (which doesn't exist in recorded form yet — see above). They
are the best real data available today, not a substitute for real production telemetry once
Section G is deployed (P1 of this close-out pass ships that deployment — see the companion PR).

## ESTIMATED inference cost per 1,000 extractions, by language/tier

| Language (tier) | Cost / call (ESTIMATED) | Cost / 1k extractions, USD (ESTIMATED) | Cost / 1k extractions, INR (ESTIMATED) |
|---|---|---|---|
| en (small) | $0.0000958 | **$0.096** | ₹9.17 |
| hi (large) | $0.0006257 | **$0.626** | ₹59.87 |
| hi-en (large) | $0.0011667 | **$1.167** | ₹111.65 |

Blended, under assumption #2 above (70/15/15 mix): **≈$0.336 / 1k extractions (ESTIMATED)**
(≈₹32.15/1k). The large tier costs roughly 6.5–12x the small tier per extraction — almost
entirely because hi/hi-en reviews route to the 70B model, not because of token-count differences
alone (hi-en's longer average length compounds this further).

## Is cost dominated by per-call inference or a fixed floor? — corrected, with real floors modeled

**The original version of this ADR was wrong to call this "decisive at any volume."** It treated
"both platforms are on free tiers" as equivalent to "fixed cost is $0," which conflated *current
state* with *structural floor* and never modeled how either platform's floor moves as volume
grows. Corrected below with three real, currently-billable-or-about-to-be fixed cost
components, live-checked 2026-07-31, not assumed:

**1. Artifact Registry — already over its free tier, right now.**
`gcloud artifacts repositories describe review-iq --location=asia-south1 --project=review-iq-prod`
reports **772.863MB** stored. The free tier is 512MB (0.5GB); Google's published rate for
overage is **$0.10/GB/month** (cloud.google.com/artifact-registry/pricing, cross-checked against
an independent summary). **Real, current, ongoing cost: ≈$0.0255/month.** This does **not**
scale with extraction volume at all (it's Docker image size, not request count) — it is a
genuine flat floor that was $0 in the original version of this ADR and is not $0 in reality.

**2. Supabase — currently free, but with a computable, real trigger point.**
Live query against production: total DB size is **12.49MB** of the 500MB free-tier ceiling
(`ops/runbooks/supabase-limits.md`). Measuring the two tables that grow with extraction
volume — `extractions` (408KB / 120 rows = **3.400 KB/row**) and `usage_records` (144KB / 289
rows = **0.498 KB/row**) — gives a combined **≈3.898 KB of DB growth per additional extraction**
(assumption #6 above). At that rate, the remaining 487.5MB of free-tier headroom is exhausted
after **≈128,000 additional cumulative extractions** (ESTIMATED, linear extrapolation). Beyond
that point, Supabase Pro's **$25/month** becomes a real, ongoing fixed cost.

**3. Cloud Run — genuinely $0 today, verified, but only because of a specific live setting.**
`gcloud run services describe review-iq ...` shows **no `minScale` annotation**, meaning the
service runs at its default `minInstances=0` (true scale-to-zero) — confirmed live, not assumed.
At today's traffic (120 total extractions ever) and even at 100,000 extractions/month, Cloud
Run's request/compute usage stays within the Always Free tier (2,000,000 requests, 180,000
vCPU-seconds, 360,000 GiB-seconds/month) — **so Cloud Run's real contribution to the fixed floor
stays $0 across every volume modeled below, as long as `minInstances` stays 0.** Priced as a
sensitivity, not a current cost: if GG ever sets `minInstances=1` to eliminate cold starts (a
real, plausible future operational choice), that adds an ESTIMATED **≈$76.31/month** (1 vCPU +
0.5GiB always-on, asia-south1 rate) — larger than the Supabase Pro floor. Not modeled into the
volume table below since it is not the current state.

### The actual fixed-vs-marginal split, by volume (ESTIMATED)

| Monthly volume | Marginal (inference) cost | Fixed cost, pre-Supabase-Pro-trigger | % marginal, pre-trigger | Cumulative months to trigger, at this rate | Fixed cost, post-trigger | % marginal, post-trigger |
|---|---|---|---|---|---|---|
| **~2 / 14 days (current real)** | $0.0014/mo | $0.0255/mo | **5.4%** | ~29,800 months (not reachable at this rate) | — | — |
| 1,000/mo | $0.336/mo | $0.0255/mo | **93.0%** | ~128 months (~10.7 years) | $25.0255/mo | 1.3% |
| 10,000/mo | $3.36/mo | $0.0255/mo | **99.2%** | ~12.8 months | $25.0255/mo | 11.8% |
| 100,000/mo | $33.60/mo | $0.0255/mo | **99.9%** | ~1.3 months | $25.0255/mo | **57.3%** |

**Two crossover points, not one:**

- **Crossover A (~76 extractions/month, ESTIMATED):** below this, the ~$0.0255/month Artifact
  Registry floor alone outweighs marginal inference cost — trivially true only at the product's
  *current* real volume (≈4.3/month), which is why the table's first row shows just 5.4%
  marginal. Above ~76/month, marginal cost already dominates, well before Supabase Pro ever
  triggers.
- **Crossover B (~74,500 extractions/month, ESTIMATED) — the one that actually matters for the
  pricing conclusion:** once cumulative volume has pushed Supabase onto its $25/month Pro plan
  (which happens within *months*, not years, at 10,000+/month sustained), the fixed floor jumps
  to ≈$25.03/month. Marginal cost only re-exceeds that floor once the *monthly* rate itself
  passes ≈74,500 extractions — a genuinely high bar, not a formality.

**Corrected conclusion: "usage-based pricing is coherent" holds only above Crossover B once
Supabase Pro has triggered — it is not true at every volume, as the original version of this
ADR claimed.** Between the Pro-trigger point and ~74,500 extractions/month (the Growth tier
proposed below, 10,000/month, sits *inside* this band), the cost structure is **majority-fixed**
(88% at 10,000/month post-trigger) — a pure per-extraction price would under-represent the real
cost structure in that band. A flat platform-fee component (which the proposed tier prices,
below, already have in the form of a monthly subscription rather than pure metered billing) is
the mechanism that already absorbs this, not an omission — but the *reasoning* for why a flat
fee matters here was missing from the original version of this ADR, and is stated now.

## Proposed tiers (recommendation, not a decision)

Building on the *existing* free-tier concept already partially implemented
(`app/main.py`'s published API docs state "Free tier: 100 requests/month" as monthly quota per
key) — **flagging a real, found discrepancy**: `app/api/admin.py`'s `CreateKeyRequest.quota`
default is actually **1000**, not 100 — whichever number is correct, the two currently disagree
and should be reconciled before any tier goes live, independent of this pricing exercise.

| Tier | Monthly extractions | Price | Worst-case marginal COGS (all hi-en, ESTIMATED) | Best-case marginal COGS (all en, ESTIMATED) | Margin, marginal-cost-only (ESTIMATED) |
|---|---|---|---|---|---|
| Free | 100 | $0 | $0.12 | $0.01 | loss-leader, by design |
| Starter | 2,000 | $19/mo | $2.33 | $0.19 | 88% |
| Growth | 10,000 | $79/mo | $11.67 | $0.96 | 85% |
| Custom / VPC | negotiated | negotiated | — | — | — (matches ADR 0004/0005's cost-moat framing: deployable inside the customer's own VPC, priced separately) |

**These margin figures are marginal-COGS-only** (the same simplification the original ADR made)
— per the corrected crossover analysis above, the Growth tier's 10,000/month sits inside the
band where fixed costs (Supabase Pro, once triggered) can be 88% of *total* cost, not just a
rounding error against marginal COGS. A single Growth customer's fixed-cost *share* depends on
how many total customers share that one $25/month Supabase instance — not computed here (needs
a real multi-tenant cost-allocation model once there are enough paying customers to make that
math meaningful; at ~20 organizations today, none paying, it would be speculative). These
margins are wide (85–99% on a marginal-cost basis) because LLM inference is cheap relative to
typical B2B SaaS willingness-to-pay — standard for usage-based AI products, not a reason to
price lower — but "wide margin on marginal cost" and "wide margin on fully-loaded cost including
amortized fixed floors" are different claims, and only the first one is asserted here. Numbers
are a starting proposal for GG to adjust against market research/competitor pricing (not done in
this pass) and real conversion data once it exists (there are currently ~20 organizations and
120 total extractions in production — far too small a base to validate willingness-to-pay
empirically yet).

## Decision

**Not made here.** GG decides on exact price points and tier names/limits. The COGS numbers and
the fixed-vs-marginal verdict above are the load-bearing facts this decision should be made
against; the specific $19/$79 figures are a starting proposal, not a commitment.

## Consequences

- **Section G's cost telemetry is being deployed as part of this same close-out pass** (P1 —
  see the companion PR, verified live with a real recorded cost row, not just merged) — real
  production COGS data starts accumulating from that point forward and should be used to
  validate or correct every ESTIMATED figure in this ADR within the first real billing cycle.
  Until then, every number above remains an estimate, not a measurement, and must be labeled as
  such anywhere it's reused.
- The `100` vs `1000` free-tier quota discrepancy needs resolving as its own small fix,
  independent of whichever tier structure is chosen.
- The corrected crossover analysis means the Growth tier's true cost coverage should be
  re-checked once Supabase Pro is live and real customer counts exist — the marginal-COGS-only
  margin figures above are not the full picture once fixed floors are shared across a real
  customer base.
- P4 (ADR 0008) implements billing against whichever tiers GG confirms — not built here.

## Alternatives considered

- **Wait for real production cost data before proposing any tier.** Rejected — the standing
  instruction explicitly asked for a proposal now, with data-thinness stated honestly rather
  than blocking on data that doesn't exist yet; the theoretical numbers here are precise and
  real-pricing-grounded, just not yet production-validated, and that distinction is preserved
  throughout rather than hidden.
- **Extrapolate from the ~2 real extractions Section F's SLO report found.** Rejected —
  explicitly what the standing instruction warned against; n=2 (or n=0 for cost specifically)
  is not load-bearing for a per-1k-extraction cost claim at any confidence level.
