# Project Spec: review-iq — Proactive Model, Phase 1: Multi-Source Ingestion + Alerting

## Goal
Flip review-iq from a passive dashboard (seller must upload + visit) to a proactive service that
WATCHES a seller's reviews and ALERTS them (email, v1) when something needs attention. Phase 1
delivers: (1) a pluggable multi-source ingestion layer — CSV (universal on-ramp), Shopify (own-store
webhooks), Google Business Profile (official review-notification API) — NO scraping; and (2) an
alerting engine that emails the seller on high-signal events (urgency spikes, fake-review clusters,
refund demands, batch-defect patterns) using the EXISTING detection engine. Free tier, $0.

## Strategic context (why this exists)
The value problem (#7): a dashboard a seller must feed and visit has thin felt value. Proactive
("we'll tell you when something's wrong") is the value + stickiness. The keystone is a LIVE review
stream. VERIFIED FACTS that shaped this design:
- Amazon sends NO per-review notification emails to sellers (confirmed via Amazon's own seller help);
  there is NO API for raw review text. Flipkart same pattern. So marketplace email-forward / API
  ingestion is NOT possible.
- Scraping marketplaces is against ToS, an arms race (proxies/CAPTCHA, breaks on page changes, costs
  money — breaks $0), legally exposed, and would POISON review-iq's compliance/data-sovereignty moat.
  EXPLICITLY OUT OF SCOPE — do not build scraping of any kind.
- LEGAL live sources DO exist with official APIs: Shopify (own-store, webhooks) and Google Business
  Profile (official review API + NEW_REVIEW notification webhook, owner-authorized). These are the
  proactive sources. CSV remains the universal manual on-ramp.

## Current state (do not break)
- `Source` Protocol already exists (app/core/ingestion/) from the flywheel work — CSVSource built;
  shopify_source/email_source were stubs. THIS is the abstraction to extend.
- Detection engine done: extraction (sentiment/topics/urgency), authenticity (precision-first),
  trends. Multi-tenant Postgres + RLS, BFF, web app. Free tier (Supabase pauses; Groq caps).
- Quota enforcement on writes intact. FROZEN: API contracts, RLS, key format.

## Scope

### In scope — Phase 1
**A. Pluggable ingestion (extend the existing Source abstraction):**
- CSV (already built) — keep as the universal on-ramp; every seller can use it with zero setup.
- **Shopify connector:** OAuth-authorized to the seller's OWN store; ingest product reviews via
  Shopify's API/webhooks (real-time on new review). Owner-consented, legal. (Confirm exact Shopify
  review API/app-scope at build — reviews may come via a review app like Judge.me/Shopify's own;
  design the connector to take a configured review source.)
- **Google Business Profile connector:** OAuth-authorized to the seller's OWN profile; use the
  official GBP review API + the NEW_REVIEW notification webhook
  (developers.google.com/my-business/content/notification-setup) for real-time push on new reviews.
  Owner-consented, legal.
- Each connector implements the Source interface; new reviews flow into the SAME extraction →
  authenticity → storage pipeline as CSV (one processing path, source-agnostic).
- NO scraping. NO marketplace (Amazon/Flipkart) ingestion (impossible legally/technically).

**B. Alerting engine (email, v1):**
- A rules layer over the existing detection output that decides when a review/event is alert-worthy:
  - high-urgency review (per the refined urgency rubric — harm signal, refund/return demand);
  - authenticity: a likely_fake / priority_review, especially a CLUSTER (multiple in a short window);
  - a spike: a complaint theme rising sharply vs. its baseline (batch-defect signal);
  - (thresholds configurable; start conservative to avoid alert fatigue).
- Email delivery (free): send the seller a clear, plain-language alert ("⚠️ A customer is demanding
  a refund and citing a safety issue — [view review]" / "3 reviews in 2 days mention the same defect
  — possible batch issue"). Vernacular-aware where the review is vernacular.
- Alert PREFERENCES: per-org settings (which event types, frequency: immediate vs daily digest, on/
  off). Default sane + conservative.
- DEDUPE / ANTI-FATIGUE: never alert twice on the same review/event; respect digest vs immediate.

### Out of scope (do NOT build)
- ANY scraping or marketplace (Amazon/Flipkart) ingestion — legally/technically impossible, off the
  table permanently.
- WhatsApp/SMS delivery (Phase 2 — email proves the loop first; design alert layer channel-pluggable
  so WhatsApp slots in later, but build only email now).
- Payments/tiers. New LLM cost patterns beyond processing ingested reviews through the existing
  pipeline.
- Any change to frozen API contracts, RLS, key format, or quota enforcement.

## Tech / cost notes (free tier)
- Ingested reviews run through the existing extraction/authenticity pipeline → they consume Groq
  quota like any processing. The alerting RULES layer is pure logic over stored results — no extra
  LLM cost. Be mindful: a live connector could ingest many reviews → quota; respect per-org quota
  (ingested reviews count toward it like uploads) and the small-first/cap-survival routing already
  built.
- Email sending: use a free-tier email path (e.g. a free transactional email tier / SMTP). NO paid
  service without escalation. If no free email path exists, escalate before assuming a paid one.
- Webhooks (Shopify, GBP) need a public endpoint on the deployed API — these connectors are only
  fully live once the API is deployed; build + test against the API, note the deploy dependency.

## Architecture
```
app/core/ingestion/
  base.py                  # existing Source Protocol
  csv_source.py            # existing
  shopify_source.py        # BUILD: OAuth + review fetch/webhook → Source
  google_business_source.py# BUILD: GBP API + NEW_REVIEW webhook → Source
app/core/alerts/
  rules.py                 # BUILD: pure functions — is this event alert-worthy? (testable)
  engine.py                # BUILD: evaluate new reviews → alerts, dedupe, respect prefs
  channels/email.py        # BUILD: email delivery (free); channel-pluggable for future WhatsApp
app/api/
  webhooks/shopify.py      # BUILD: receive Shopify review webhooks (verify signature)
  webhooks/google.py       # BUILD: receive GBP NEW_REVIEW notifications (verify)
  bff/alerts.py            # BUILD: alert preferences GET/PUT (per-org)
supabase/migrations/
  <new>_alerts.sql         # BUILD (ESCALATE): alert_preferences + alert_log (dedupe) tables + RLS
```

## Data model (migration — escalation-gated)
- `alert_preferences` (org_id, event_type, enabled, frequency [immediate|daily_digest], updated_at)
- `alert_log` (org_id, review_id, event_type, sent_at) — for dedupe + digest batching. RLS, WITH
  CHECK + anon-deny (same pattern as corrections).

## Decision authority (autonomous per CHARTER.md)
ESCALATE: the alerts migration (DDL + RLS shown, isolation proof); any email/connector service that
costs money (find a FREE path or escalate); OAuth app registration / external credentials (Shopify
app, Google Cloud project) — these need GG's accounts, so surface what GG must set up; any
frozen-contract change. Connectors + rules + email logic in code = autonomous.

## Hard rules
- NO scraping, NO Amazon/Flipkart ingestion — ever. Legal/consented sources only.
- New connectors flow into the EXISTING processing pipeline — one source-agnostic path, no parallel
  extraction logic.
- Ingested reviews respect per-org quota (count like uploads); use existing cap-survival routing.
- Alert rules conservative by default (avoid fatigue); dedupe so no double-alerts.
- Webhook endpoints verify signatures/authenticity (don't trust unsigned POSTs).
- Email via a FREE path; escalate if none exists rather than incurring cost.
- Frozen contracts/RLS/quota intact. $0. Full suite green.

## Budget
- This is a multi-part phase. Soft target: 3-4 CC sessions. Hard cap: escalate after 25 executor
  invocations per session. /cost at midpoints. Build in this order so value lands incrementally.

## Success criteria (Phase 1)
- [ ] Source abstraction extended: CSV (existing) + Shopify + Google Business connectors, each
      implementing the Source interface, flowing into the existing extraction→authenticity→storage
      pipeline. NO scraping anywhere in the codebase.
- [ ] Shopify + GBP connectors are OAuth/owner-consented to the seller's OWN store/profile; webhook
      endpoints verify signatures.
- [ ] Alert rules engine flags high-urgency / fake-cluster / spike events from existing detection
      output; pure-function rules are unit-tested; conservative defaults.
- [ ] Email alerts deliver plain-language, vernacular-aware notifications via a FREE email path;
      dedupe prevents double-alerts; per-org preferences (event types, immediate vs digest, on/off).
- [ ] alerts migration applied (escalated, isolation-proven); RLS WITH CHECK + anon-deny.
- [ ] Ingested reviews respect per-org quota; no new uncapped LLM cost.
- [ ] Frozen contracts/RLS/quota intact; full suite green; $0 (no paid service without escalation).

## Build order
1. Extend Source abstraction + the alert RULES layer (pure functions, fully testable, no external
   deps, no cost) FIRST — this is the logic core, builds with zero setup/quota.
2. Alerts migration (ESCALATE: DDL + RLS + isolation proof) → alert_preferences + alert_log.
3. Email channel (free path; escalate if none) + alert engine (evaluate → dedupe → send) +
   /bff/alerts preferences. Test end-to-end with CSV-ingested reviews triggering alerts (no
   connector setup needed to prove the alert loop).
4. Shopify connector (OAuth + webhook, signature-verified). Surface to GG what to register
   (Shopify app credentials).
5. Google Business connector (GBP API + NEW_REVIEW webhook, signature-verified). Surface what GG
   must set up (Google Cloud project / OAuth).
6. Verify: full suite, isolation proof, dedupe, quota-respect; report what GG must configure
   (OAuth apps) and the deploy dependency for webhooks.
