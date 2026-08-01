# ADR 0008: Minimum-Viable Billing — Stripe Checkout + Portal, Not a Custom Payment Stack

**Status:** Accepted — implemented in this ADR's own PR, but **unverified against a live Stripe
account** (none exists — see Consequences and the escalation steps below).
**Date:** 2026-07-31
**Scope:** Wave 2 P4. Implements billing against the tiers ADR 0007 (P3) priced. Explicitly
scoped down per the standing instruction ("do not gold-plate... there are no paying customers
yet and the dashboard converts, not the billing page").

## Context

ADR 0007 proposed Free/Starter/Growth tiers with real COGS-derived margins. This ADR builds the
minimum code needed to actually charge for Starter/Growth, without inventing a payment UI, a
custom subscription-management page, or custom dunning/retry logic — all three already exist as
Stripe's own hosted, PCI-scope-reducing products, and building them ourselves would be exactly
the "gold-plating" the standing instruction warned against.

## Decision

1. **Stripe Checkout** (hosted, Stripe's own page) for signup+payment — one endpoint,
   `POST /account/billing/checkout`, creates a session and returns its URL for the frontend to
   redirect to. No custom payment form, no card-handling code in this codebase at all (Stripe
   Checkout is PCI SAQ-A eligible for exactly this reason).
2. **Stripe Customer Portal** (hosted) for self-service plan changes, cancellation, payment
   method updates, and invoice history — one endpoint, `POST /account/billing/portal`. This is
   what makes "do not gold-plate" achievable: no custom subscription-management UI is built.
3. **Stripe's own Smart Retries + dunning emails** (dashboard-configured, not code) for failed
   payments. `invoice.payment_failed` is logged for visibility only — the retry schedule and
   customer-facing dunning emails are Stripe's own feature, turned on in the dashboard (see
   escalation steps), not reimplemented here.
4. **One webhook handler**, `POST /webhooks/stripe`, for exactly four events:
   `checkout.session.completed`, `customer.subscription.updated`,
   `customer.subscription.deleted`, `invoice.payment_failed` (logged only). Signature-verified
   before any parsing, matching the pattern already established for Google/Shopify webhooks.
5. **Subscription state lives on the existing `organizations.plan` column**
   (`supabase/migrations/20260731000003_billing_subscription_state.sql` adds `starter`/`growth`
   alongside the existing `free`/`pro`/`enterprise` values, plus `stripe_customer_id`,
   `stripe_subscription_id`, `subscription_status`, `current_period_end`) — one source of truth,
   not a parallel billing-state table.
6. **A real wiring detail found, not assumed**: quota enforcement (`app/auth/api_key.py`) reads
   `api_keys.quota`, a per-key column — **not** `organizations.plan`. The webhook handler
   (`billing_storage.sync_subscription_pg`) updates both together on every plan change, or a
   paying customer would still be capped at their prior (likely free-tier) quota after upgrading.

## Consequences — the honest one

**This code has never been exercised against a live Stripe account.** No `STRIPE_SECRET_KEY`
exists anywhere in this project (checked). Every Stripe SDK call is written to match the
documented request/response shape in Stripe's own API reference, and the webhook signature
verification path is unit-tested against a real, independently-constructed HMAC-SHA256 test
vector (Stripe's own published signing algorithm — `tests/unit/test_billing.py`, 13 tests, all
passing) — not a live account, but a genuine cryptographic proof, not an assumption. Checkout
and Portal session creation are tested with the Stripe SDK layer mocked, confirming this code
constructs the correct request and handles the response correctly — not that Stripe's API
actually accepts it. **Do not treat this as "tested" in the sense every other piece of this
Wave has been** — it needs a real Stripe test-mode run before going anywhere near a real
customer. See the PR's escalation steps for exactly what that requires.

## Alternatives considered

1. **Build a custom payment form + subscription-management UI.** Rejected — this is precisely
   the gold-plating the standing instruction warned against, and it would additionally bring
   this codebase into full PCI DSS scope (SAQ-D) instead of Stripe Checkout's SAQ-A. No upside
   for a pre-launch product with zero paying customers.
2. **Custom dunning/retry logic (a `payment_retry_schedule` table, cron job, templated emails).**
   Rejected — Stripe's Smart Retries already does this, tuned on real aggregate payment-recovery
   data across their entire platform; reimplementing it here would be strictly worse and is
   pure scope creep for a "minimum viable" pass.
3. **A separate `subscriptions` table instead of extending `organizations`.** Rejected —
   `organizations.plan` already existed as the single source of truth for "what plan is this
   org on"; a second table tracking the same concept would create a reconciliation problem
   (which one wins if they disagree?) for no benefit at this schema's size.
4. **Wait until a Stripe account exists to write any code.** Rejected — the standing instruction
   asked for P4 to be built now; the code is written correctly against Stripe's documented
   contract and unit-tested wherever that's genuinely possible without live credentials. Waiting
   would mean nothing is ready the moment a Stripe account does exist.

## Escalation — I cannot do any of this myself

**Step 1 — Create the Stripe account** (if one doesn't already exist): go to
`https://dashboard.stripe.com/register`, complete business verification. This is an irreversible,
identity-tied action only GG can take.

**Step 2 — Create the two prices**, matching ADR 0007's proposed tiers (adjust amounts if GG
changes them): Stripe Dashboard → Product catalog → Add product.
- Product "Starter": recurring price, $19.00/month USD (or adjust). Copy the resulting Price ID
  (`price_...`).
- Product "Growth": recurring price, $79.00/month USD (or adjust). Copy its Price ID.

**Step 3 — Enable Smart Retries + dunning emails**: Dashboard → Settings → Billing → Automatic
collection. Turn on "Smart Retries" and "Send emails about upcoming/failed payments" — this is
the entire dunning mechanism this ADR relies on; without this step, `invoice.payment_failed`
still logs but Stripe won't retry or email the customer.

**Step 4 — Register the webhook endpoint**: Dashboard → Developers → Webhooks → Add endpoint.
- Endpoint URL: `https://<your-api-domain>/webhooks/stripe`
- Events to send: `checkout.session.completed`, `customer.subscription.updated`,
  `customer.subscription.deleted`, `invoice.payment_failed`
- Copy the resulting **Signing secret** (`whsec_...`).

**Step 5 — Set environment variables** (test-mode keys first, always — never paste a live
`sk_live_...` key anywhere until Step 8 below is fully verified):
```bash
gcloud run services update review-iq --region=asia-south1 --project=review-iq-prod \
  --update-env-vars STRIPE_PRICE_ID_STARTER=price_xxx,STRIPE_PRICE_ID_GROWTH=price_yyy,BILLING_RETURN_URL=https://app.samidhareviews.xyz/account
gcloud secrets create stripe-secret-key --project=review-iq-prod --data-file=- <<< "sk_test_xxx"
gcloud secrets create stripe-webhook-secret --project=review-iq-prod --data-file=- <<< "whsec_xxx"
gcloud run services update review-iq --region=asia-south1 --project=review-iq-prod \
  --update-secrets=STRIPE_SECRET_KEY=stripe-secret-key:latest,STRIPE_WEBHOOK_SECRET=stripe-webhook-secret:latest
```

**Step 6 — Apply the migration** (same process as every other migration in this repo — see
prior Wave PRs' escalation steps for the general pattern):
```bash
psql "$SUPABASE_DIRECT_URL" -f supabase/migrations/20260731000003_billing_subscription_state.sql
```

**Step 7 — Reconcile the free-tier quota discrepancy** (flagged in ADR 0007, not fixed there):
decide whether the free tier is 100 or 1000 extractions/month, then fix whichever of
`app/main.py`'s docs, `app/api/admin.py`'s `CreateKeyRequest.quota` default, or
`app/api/account.py`'s `_do_regenerate` hardcoded `100` is wrong — currently all three disagree.

**Step 8 — Verify end-to-end in Stripe TEST mode before touching anything live**:
```bash
# Use Stripe CLI's test-mode webhook forwarding + a test card (4242 4242 4242 4242)
stripe listen --forward-to localhost:8001/webhooks/stripe
# In another terminal, trigger a real test checkout via POST /account/billing/checkout,
# complete it with the test card, confirm:
#   1. organizations.plan updates to 'starter'/'growth'
#   2. api_keys.quota updates to match (2000/10000)
#   3. subscription_status becomes 'active'
# Then cancel via the Portal, confirm plan reverts to 'free' and quota to 100 (or 1000, per Step 7).
```
Only after Step 8 passes should `sk_live_...`/live webhook secret ever be considered.

## Testing (what was actually run)

- `ruff check .` / `mypy app/` — pass repo-wide, including the new billing modules.
- `pytest tests/unit/test_billing.py` — 13/13 pass: signature verification against a real
  independently-constructed HMAC test vector (valid, forged, tampered, and expired-timestamp
  cases all correctly accepted/rejected), checkout/portal session request construction (Stripe
  SDK mocked), plan-to-price mapping.
- `pytest tests/` (full suite, excluding integration/benchmark) — 1121/1121 pass.
- Migration dry-run verified against production in a rollback-only transaction (same MVCC-safe
  technique used throughout this Wave): all 20 existing organizations (all on `plan='free'`)
  remain valid under the widened CHECK constraint; new columns and unique index confirmed
  present.
- Re-ran the Wave 2 P1 ACL exposure checker against the widened `organizations` schema: clean.
- **Not run**: anything requiring a live Stripe account (Steps 1–8 above are all outstanding).
