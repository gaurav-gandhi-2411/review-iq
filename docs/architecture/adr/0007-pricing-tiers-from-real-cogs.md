# ADR 0007: Pricing Tiers — Derived from Real Inference Cost, Not Guessed

**Status:** Proposed — a pricing recommendation for GG to review, not an executed decision.
**Date:** 2026-07-31
**Scope:** Wave 2 P3. Prices tiers from real, verified inference-cost data; explicitly does not
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
Section G is deployed.

## Cost per 1,000 extractions, by language/tier (theoretical, from the above)

| Language (tier) | Cost / call | Cost / 1k extractions (USD) | Cost / 1k extractions (INR) |
|---|---|---|---|
| en (small) | $0.0000958 | **$0.096** | ₹9.17 |
| hi (large) | $0.0006257 | **$0.626** | ₹59.87 |
| hi-en (large) | $0.0011667 | **$1.167** | ₹111.65 |

Blended, assuming an illustrative 70% en / 15% hi / 15% hi-en mix (a planning assumption, not a
measured distribution — no real customer language mix exists yet either): **≈$0.336 / 1k
extractions** (≈₹32.15/1k). The large tier costs roughly 6.5–12x the small tier per extraction —
almost entirely because hi/hi-en reviews route to the 70B model, not because of token-count
differences alone (hi-en's longer average length compounds this further).

## Is cost dominated by per-call inference or a fixed floor?

**Per-call inference, decisively, at any volume this product is likely to see in the near-to-
medium term.** Both underlying platforms are on free tiers today with **zero fixed cost being
paid right now**:

- **Cloud Run**: Always Free covers 2,000,000 requests/month, 180,000 vCPU-seconds,
  360,000 GiB-seconds (`ops/runbooks/cloud-run-cost-check.md`) — at this product's actual
  current traffic (120 total extractions ever recorded, per a live count during this
  session's recon), this ceiling is not remotely close to binding.
- **Supabase**: Free plan, 500MB DB / 5GB egress/month (`ops/runbooks/supabase-limits.md`);
  Pro ($25/mo) is the documented upgrade trigger, not yet reached.

Since the *marginal* cost (LLM inference) scales linearly with usage while the *fixed* cost is
currently $0 and, even after a Supabase Pro upgrade, is a flat $25/month regardless of
extraction volume, **usage-based/volume pricing is coherent** — the thing that actually costs
more as a customer sends more reviews (LLM tokens) is exactly the thing volume pricing would
charge for. The fixed floor only becomes a meaningful share of total cost at a customer volume
far beyond anything currently observed, and even then it's a bounded, shared platform cost, not
a per-customer marginal one.

## Proposed tiers (recommendation, not a decision)

Building on the *existing* free-tier concept already partially implemented
(`app/main.py`'s published API docs state "Free tier: 100 requests/month" as monthly quota per
key) — **flagging a real, found discrepancy**: `app/api/admin.py`'s `CreateKeyRequest.quota`
default is actually **1000**, not 100 — whichever number is correct, the two currently disagree
and should be reconciled before any tier goes live, independent of this pricing exercise.

| Tier | Monthly extractions | Price | Worst-case COGS (all hi-en) | Best-case COGS (all en) | Margin (worst-case) |
|---|---|---|---|---|---|
| Free | 100 | $0 | $0.12 | $0.01 | loss-leader, by design |
| Starter | 2,000 | $19/mo | $2.33 | $0.19 | 88% |
| Growth | 10,000 | $79/mo | $11.67 | $0.96 | 85% |
| Custom / VPC | negotiated | negotiated | — | — | — (matches ADR 0004/0005's cost-moat framing: deployable inside the customer's own VPC, priced separately) |

These margins are wide (85–99% depending on language mix) because LLM inference is cheap
relative to typical B2B SaaS willingness-to-pay for structured insight + analytics + dashboard
access — this is standard for usage-based AI products where compute is a small fraction of
price, not a reason to price lower. Numbers are a starting proposal for GG to adjust against
market research/competitor pricing (not done in this pass) and real conversion data once it
exists (there are currently ~20 organizations and 120 total extractions in production — far too
small a base to validate willingness-to-pay empirically yet).

## Decision

**Not made here.** GG decides on exact price points and tier names/limits. The COGS numbers and
the fixed-vs-marginal verdict above are the load-bearing facts this decision should be made
against; the specific $19/$79 figures are a starting proposal, not a commitment.

## Consequences

- Section G's cost telemetry should be merged and deployed before any tier launches, so real
  production COGS can validate (or correct) the theoretical numbers above within the first
  billing cycle.
- The `100` vs `1000` free-tier quota discrepancy needs resolving as its own small fix,
  independent of whichever tier structure is chosen.
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
