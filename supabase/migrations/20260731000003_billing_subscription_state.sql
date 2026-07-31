-- Migration: minimal Stripe subscription state on organizations.
--
-- Wave 2 P4 (minimum-viable billing, ADR 0008). Extends the EXISTING
-- organizations.plan column (already present since 20260510000001_create_tables.sql,
-- CHECK-constrained to 'free'/'pro'/'enterprise') rather than inventing a parallel
-- concept -- one source of truth for "what plan is this org on."
--
-- 'starter' and 'growth' are ADDED alongside the existing values (not replacing them)
-- -- 'pro'/'enterprise' stay valid for any row that might already use them; this repo's
-- own account.py and admin.py both default new orgs to 'free' today, so in practice no
-- row is expected to need migrating, but the constraint change itself must not be able
-- to reject an existing row.
--
-- NOT applied by this session -- same convention as every other migration in this repo,
-- GG applies via the same process as prior migrations (see the PR body's escalation
-- steps for the Stripe-specific setup this depends on).

ALTER TABLE public.organizations
  DROP CONSTRAINT IF EXISTS organizations_plan_check;

ALTER TABLE public.organizations
  ADD CONSTRAINT organizations_plan_check
  CHECK (plan IN ('free', 'starter', 'growth', 'pro', 'enterprise'));

-- Stripe identifiers -- nullable: an org on the free plan never talks to Stripe at all,
-- so these stay NULL until (if ever) that org starts a paid subscription.
ALTER TABLE public.organizations
  ADD COLUMN IF NOT EXISTS stripe_customer_id text,
  ADD COLUMN IF NOT EXISTS stripe_subscription_id text,
  ADD COLUMN IF NOT EXISTS subscription_status text
    CHECK (subscription_status IS NULL OR subscription_status IN (
      'active', 'past_due', 'canceled', 'incomplete', 'incomplete_expired',
      'trialing', 'unpaid', 'paused'
    )),
  ADD COLUMN IF NOT EXISTS current_period_end timestamptz;

-- One org maps to at most one Stripe customer -- prevents a bug where two orgs
-- accidentally share a customer_id (which would let one org's checkout webhook
-- silently update the wrong org's plan).
CREATE UNIQUE INDEX IF NOT EXISTS organizations_stripe_customer_id_key
  ON public.organizations (stripe_customer_id)
  WHERE stripe_customer_id IS NOT NULL;

COMMENT ON COLUMN public.organizations.stripe_customer_id IS
  'Stripe Customer ID (cus_...). NULL until this org starts a Stripe Checkout session.';
COMMENT ON COLUMN public.organizations.stripe_subscription_id IS
  'Stripe Subscription ID (sub_...). NULL until checkout.session.completed fires.';
COMMENT ON COLUMN public.organizations.subscription_status IS
  'Mirrors Stripe''s own subscription status enum exactly (see Stripe API docs,
   Subscription object, "status" field) -- kept in sync by the webhook handler
   (app/api/webhooks/stripe.py), never written anywhere else. Source of truth for
   whether this org''s plan-based quota should be enforced as paid or fallen back
   to free-tier limits.';
COMMENT ON COLUMN public.organizations.current_period_end IS
  'End of the current billing period, from Stripe''s subscription object. Used for
   display only (e.g. "renews on..."), not for quota enforcement -- subscription_status
   is the enforcement signal.';
