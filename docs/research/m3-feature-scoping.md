# M3 feature scoping: ingestion, retention, reply drafting

Date: 2026-10-07. Base: `origin/main` at `a08fff0`. Read-only scoping: no code, DDL, prod writes,
live LLM calls or secrets. Labels: VERIFIED = read in this repo at that SHA, or a cited primary
page; BELIEVED = third-party summary or inference, with how to confirm.

Headline: **M3c is already built** (the "Read and draft a reply" link in #263 opens a working
drafting feature). **M3b is half built** (daily digest, unsubscribe, Resend exist; weekly digest
and a real-time urgent path do not). **M3a Shopify is code-complete but unreachable** (no UI, no
deploy config, never exercised against a real store).

## M3a. Ingestion

### Shopify: what exists (VERIFIED, repo at a08fff0)

| Piece | Where | State |
|---|---|---|
| Table `shopify_installations` (org_id FK cascade, `shop_domain` UNIQUE, Fernet `access_token_enc`, `revoked_at`) | `supabase/migrations/20260622000001_shopify_installations.sql`; RLS select-only for `authenticated` | Applied by migration history; 9 RLS tests in `tests/integration/test_shopify_installations_rls.py` |
| Write path | SECURITY DEFINER `upsert_shopify_installation()` in `20260801000002_tenant_resolvers_auth_signup.sql` (BYPASSRLS remediation 2c) | Done |
| `GET /auth/shopify/begin`, `POST /auth/shopify/callback` | `app/api/shopify_auth.py` | HMAC mandatory, stateless 10-min CSRF state, org_id from JWT only, token exchange, encrypt, upsert, best-effort webhook registration |
| `POST /webhooks/shopify/reviews` | `app/api/webhooks/shopify.py` | HMAC check before parse, org lookup by shop domain, unknown shop returns 200 and drops, background extraction; MultiFernet key rotation supported |
| `ShopifySource.fetch_reviews()` (GraphQL `product_review` metaobjects, paged by 50) | `app/core/ingestion/shopify_source.py` | **Not called anywhere**; only the webhook imports `_node_to_review_row`. No backfill and no polling |
| Tests | `tests/unit/test_shopify_auth.py` (33), `test_shopify_connector.py` (36), RLS integration (9) | Unit tests are mocked HTTP |
| Routers mounted | `app/main.py` lines 250-251, public service only | Live in code, unconditional (no feature flag) |
| Runbook | `ops/runbooks/secret-rotation.md` covers the two Shopify secrets | |

### Shopify: what is missing

1. **No frontend.** `grep -i shopify web/src` returns nothing: no "Connect store" button, no SPA callback route that captures `code/shop/state/hmac` and POSTs them. The OAuth flow cannot start from the product.
2. **No deploy config.** `SHOPIFY_CLIENT_ID/SECRET`, `SHOPIFY_TOKEN_ENCRYPTION_KEY`, `SHOPIFY_WEBHOOK_BASE_URL` appear nowhere in `.github` or `cloud-run-deploy.md`. If unset, `/begin` returns 503 (fail-safe). Whether prod has them set is unverified (no gcloud call made).
3. **A real Shopify Partner app does not exist** (GG item in `shopify_source.py` docstring): partner account, app, redirect URL, Shopify's app review.
4. **Redirect mismatch risk.** `/begin` builds `redirect_uri` as `{webhook_base_url}/auth/shopify/callback`, a backend GET path, but the callback is a POST the SPA must make. The docstrings say Shopify redirects to the SPA. These two designs disagree; one must be fixed before a first install.
5. Scope string requests `write_product_reviews` although the connector only reads (least-privilege issue; Shopify may flag it in app review).
6. Reviews exist in Shopify only if the merchant runs a review app that writes the Standard Product Review metaobject (Judge.me, Loox...). Native Shopify reviews were shut down May 2024 (BELIEVED, from prior research recorded in `shopify_source.py`; not re-fetched today).
7. No uninstall webhook (`app/uninstalled`), so `revoked_at` is never set automatically. Shopify's mandatory GDPR webhooks (customers/data_request, customers/redact, shop/redact) for public apps are absent (BELIEVED requirement; confirm in Shopify app-review docs).
8. Webhook registration passes topic `metaobjects/create` without a `type:product_review` filter, so every metaobject creation in the shop hits the endpoint (it should be dropped by `_parse_webhook_payload`; confirm).

Effort to a first real install (BELIEVED estimate): frontend connect + callback route 1 day;
fix redirect design + scopes 0.5 day; backfill job using `ShopifySource` through the existing
ingest queue 1-1.5 days; uninstall + GDPR webhooks 1 day; Partner app setup and dev-store test
with GG; Shopify review is calendar time outside our control. About 4-5 engineering days plus
the external review.

### Amazon.in: options

| Option | Fact | Label |
|---|---|---|
| SP-API Customer Feedback API | Returns aggregated review topics and trends, **not individual review text**; needs "Brand Analytics" or "Selling Partner Insights" role; marketplaces listed: US, UK, FR, IT, DE, ES, JP, **India not listed** | VERIFIED (Amazon doc: developer-docs.amazon/sp-api/docs/customer-feedback-api-v2024-06-01-use-case-guide) |
| SP-API fees | Announced Nov 2025 ($1,400/yr + GET overage $0.40/1K); **cancelled 12 May 2026** "at this time" (may return) | VERIFIED via secondary reporting (novadata.io/resources/news/amazon-cancels-sp-api-fees-may-2026, ppc.land); Amazon's own notice not read |
| SP-API raw review text for sellers | No endpoint found in search results; developers report 403 on feedback endpoints | BELIEVED (search summaries, github.com/amzn/selling-partner-api-models issue 4926) |
| Scraping | Amazon Conditions of Use bar "data mining, robots, or similar data gathering and extraction tools" | BELIEVED (conductatlas.com summary of the .com terms; amazon.in text not read). Risk includes contract claims and account/IP blocking; Indian IT Act s.43 (unauthorised access) is arguable but untested for public pages (BELIEVED: law.asia, ssrana.in summaries) |
| Third-party scrape APIs | Rainforest API: $59/mo for 10K requests, $375/mo for 250K (about $1.50/1K at volume); Apify review actors about $0.64-$2.00 per 1,000 reviews | BELIEVED (vendor pages via search summaries; amazon.in coverage not confirmed per vendor) |

Verdict (judgment): there is **no sanctioned way to pull a seller's Amazon.in review text through
an official API today**. Cheapest honest path: keep CSV upload (exists) plus the seller exporting
reviews they are entitled to see. Using a third-party scraping vendor moves legal risk to the
vendor contractually but not to us in practice; it also adds roughly $1.50-$2 per 1,000 reviews
(at 10K reviews/mo about $15-$20/mo per customer, which is 100%+ of a $15 Starter's gross margin
room), and Amazon changes markup often (maintenance burden: expect breakage monthly, BELIEVED).
Recommendation: do not build Amazon scraping; revisit only if Amazon opens a review endpoint for
India.

### Flipkart: options

| Option | Fact | Label |
|---|---|---|
| Seller API | Sections: Listing, Order, Report, Notification management. **No ratings/reviews endpoint** documented. OAuth2: self-access client credentials (token about 60 days) and third-party authorization-code with refresh tokens | VERIFIED (seller.flipkart.com/api-docs/FMSAPI.html fetched 2026-10-07) |
| Scraping | Terms prohibit "page-scrape, robot, spider ... or any similar ... process" | BELIEVED (quoted in search summary; Flipkart terms page itself not fetched) |
| Third-party | AnyPage-style `/flipkart/products/reviews` APIs, Apify actors | BELIEVED; pricing not gathered |

Verdict: same as Amazon. The Seller API is the right integration for orders and not for reviews.
Note the repo already holds a licensed Flipkart research corpus (per memory), which is for
evaluation, not a customer ingestion path.

Net for M3a: ranked by value per effort, (1) finish Shopify (only channel with an official
review read path, though gated on the merchant's review app), (2) improve CSV upload UX/templates
for Amazon/Flipkart exports, (3) defer marketplace scraping.

## M3b. Retention: weekly digest and urgent alerts via Resend

### What exists (VERIFIED, repo)

- **Daily digest**: `app/core/alerts/digest.py` (batches `high_urgency` and `likely_fake` per org, dedupes via `alert_log`, records only after a successful send, one email per org), `app/api/internal/digest.py` (`POST /internal/digest/run`, token header `X-Digest-Trigger-Token`, per-org failure isolation). Cloud Scheduler job `review-iq-digest-daily`, cron `0 2 * * *` Asia/Kolkata.
- **Scheduler state is contradictory in the docs**: `clean-break-migration.md` says PAUSED, `scripts/toggle_scheduler_jobs.sh` says paused by default, but project memory records both jobs ENABLED and proven firing. Unverified today; confirm with `scripts/toggle_scheduler_jobs.sh status`.
- **Immediate alerts**: `app/core/alerts/engine.py` evaluates rules per review at ingest (high_urgency, likely_fake, fake_cluster, topic_spike, batch_defect, fake_campaign) with per-event-type preferences (`enabled`, `frequency` in {immediate, daily_digest}), dedupe, and `ResendChannel`.
- **Unsubscribe**: HMAC token per org, `GET/POST /unsubscribe`, `List-Unsubscribe` + one-click headers, clears `organizations.notification_email` (single choke point). Preferences API: `app/api/bff/alerts.py`.
- **Sender**: Resend; live default is the sandbox sender (`onboarding@resend.dev`, delivers only to the account owner). Custom-domain runbook exists (`docs/email-deliverability-runbook.md`); memory says samidhareviews.xyz migration completed, but whether the verified domain is the live `RESEND_FROM_EMAIL` is unverified.

### What remains

1. **Weekly digest.** `_VALID_FREQUENCIES` is only `immediate` and `daily_digest` (`bff/alerts.py:33`). Needs a `weekly_digest` frequency (CHECK constraint migration if one exists on `alert_preferences.frequency` - verify), a watermark per cadence, a scheduler job (weekly cron), and a richer body than the current event list: week-over-week counts, top complaint topics, urgent items, and a link into the dashboard. About 2-3 days, mostly template and query work; the batching/dedupe machinery is reusable.
2. **Urgent alerts that are actually urgent.** The immediate path already sends high_urgency per review, but only when extraction runs and with the subject templates above; batch-ingested CSVs may flood (one email per event). Needs a per-org rate cap/coalescing (for example max 1 urgent email per 15 minutes with a roll-up). About 1 day.
3. **Quota awareness**: leads and alerts share Resend's 100/day cap (`leads.py` comment). Daily digests scale with customers, not reviews.
4. Delivery observability: no bounce/complaint webhook handling found (Resend webhooks); needed before volume. About 1 day.
5. Reply-to/from identity and domain verification must be confirmed live (GG).

### Cost

Resend (VERIFIED, resend.com/pricing fetched 2026-10-07): Free $0, 3,000 emails/month, 100/day, 3 domains; Pro $20/month for 50K emails (or $35 for 100K), no daily limit, overage $0.90 per 1,000. Marginal email cost on Pro: $0.0004 (20/50,000); on Free: $0.

Volume model (assumptions: 1 daily digest per customer at most, urgent alerts coalesced to 5/day worst case): at 20 customers daily digest = 600/month plus alerts up to 3,000 -> near the free cap at 20 customers only in the worst case; at 40 customers worst case exceeds 3,000 and 100/day. Weekly digests (4/month/customer) cost 80-160 emails at 20-40 customers, negligible. So Resend stays free until roughly 40-90 customers depending on alert volume; Pro adds $20/month (reflected in cost-model section 7.4's second column).

Total M3b effort: about 4-6 days. Cost impact: $0 until volume; no LLM cost.

## M3c. Response drafting

### What exists behind "Read and draft a reply" (VERIFIED)

The link (`ReviewRow.tsx` on `feat/s17-dashboard-recency`, PR #263) routes to `/reviews/:id`, which on `main` already has a full "Draft a reply" panel (`web/src/pages/ReviewDetail.tsx`, line 201): tone selector, "Draft reply" button, loading spinner, rate-limit message, copy button. It calls `draftReply()` (`web/src/lib/api.ts`) -> `POST /bff/reply` (`app/api/bff/router.py:508`) -> `draft_reply()` (`app/core/reply/engine.py`).

Backend behavior: sanitize and wrap review (PII redaction, injection neutralization); language detect; grounding from the review's cons/topics (the UI passes the existing extraction, so no second extraction call); prompt v2.1 with four tones x intensity matching x Hinglish rules (`app/core/prompts/reply.py`); **always the large model** (`openai/gpt-oss-120b`), degrading to the small model on quota only for English; **Hindi and Hinglish return 503 rather than use the small model** (it composed incoherent text); guardrails (fabricated-commitment regexes, language match, length 30-2000 chars, keyword grounding for English) that become `caveats`, never blocks; signature appended; per-process in-memory cache; token usage recorded via `update_usage_tokens`. Also `POST /v2/reply` and a batch endpoint (API-key path). Tests: `tests/test_reply_engine.py`, `test_reply_guardrails.py`, `tests/unit/test_bff_reply_graceful.py` (49 test functions together). Eval: `eval/reply/runner.py` over 15 fixtures (5 en, 5 hi, 5 hi-en), structural guardrails only.

Gaps: no human-in-the-loop send (copy only, fine); **no quality eval** (guardrails check shape, not helpfulness, tone, or factual safety; n=15; no ratings); no per-org draft rate limit; no `extraction_costs` row for drafts (cost telemetry covers extraction only; `extract.py` is the only `price_extraction` caller besides demo); cache is lost per instance; guardrail fabrication regexes are English-only so Hindi/Hinglish promises ("refund de denge") pass unchecked.

### Cost per draft (current models, `app/core/pricing.py`, VERIFIED rates as_of 2026-09-10)

Tokens: prompt size measured offline by building the real prompt for the 15 fixtures (no LLM call): mean 4,264 characters (en 3,689; hi 3,635; hi-en 5,468). Calibrated at 3.45 characters/token from the extraction prompt (6,047 characters for a short review vs the measured mean of 1,751 input tokens in `eval/results/token_cost_measurement_n106.json`) -> **about 1,240 input tokens** (en 1,070; hi-en 1,585). BELIEVED: the reply cassettes are not in the committed store (no `reply_text` entries in `eval/cassettes/*.json`), so real reply token counts have **not** been measured; output assumed 500 tokens (300-800), by analogy to the 647-token measured extraction output of the same reasoning model, which includes hidden reasoning tokens.

| Model | Formula | Cost per draft |
|---|---|---|
| gpt-oss-120b (default) | 1,236 x $0.15/M + 500 x $0.60/M | **$0.000485** (Rs 0.046); range $0.000365-$0.000665 |
| gpt-oss-20b (English degradation) | 1,236 x $0.075/M + 500 x $0.30/M | $0.000243 |
| Llama 3.3 70B on OpenRouter (failover) | 1,236 x $0.10/M + ~250 x $0.32/M | about $0.00020 (BELIEVED, rates unverified) |

A draft costs about 0.9x one blended extraction ($0.000534). At 10% of a 10,000-review Starter
customer's reviews drafted, that is 1,000 drafts x $0.000485 = $0.49/month per customer (BELIEVED
inputs), under 1% of a $59 price.

### Quota impact

Verified pool: 200K tokens/day per model, 8K TPM (ADR 0015, `capacity_model.json`). The "100K
tokens/model/day" figure in the brief is the self-imposed 50%-of-pool ceiling used for eval runs
(ADR 0016, ADR 0031); I use 200K as the hard limit and 100K as the working budget. A draft is about
1,740 tokens on the large pool.

- Alone on the large pool: 200K / 1,740 = **115 drafts/day** at the hard limit, 57/day inside the 100K working budget.
- The large pool is already the binding tier for extraction (140.6 extractions/day, `capacity_model.json`). One draft consumes about 0.73 of an extraction's large-pool tokens (1,740 / 2,394), so every 100 drafts/day removes about 73 extractions/day.
- TPM: 8K / 1,740 = 4.6 drafts/minute; a double-click or batch of 20 hits 429 immediately (the UI already shows a "try again in a minute" message).
- Verdict: on the free tier a few customers' reply usage would crowd out extraction. This is the same Developer-plan prerequisite as section 6 of `docs/cost-model.md`: reply drafting is not a separate quota problem, but it must not ship ahead of the plan upgrade at any real volume. Hindi/Hinglish additionally cannot degrade, so a quota cap is a user-visible 503.

### Quality risks (ordered)

1. Fabricated commitments in Hindi/Hinglish pass the English-only regex guard (refunds, timelines).
2. Brand-damaging tone on edge cases (sarcasm, abusive reviews, legal threats, safety complaints); the prompt has no escalation path (a draft is produced for everything).
3. Hinglish register mismatch; known fixed once via prompt v2.0 but never measured with ratings.
4. Prompt injection via review text: mitigated by sanitize/wrap and the S15d controls, not tested against the reply prompt specifically (the injection suite targets extraction).
5. Caveats are shown but a guardrail failure never blocks, so a flagged draft can be copied unchanged.

### Eval plan (rule 82: designed with the feature)

1. Fixtures: grow `eval/reply/fixtures` from 15 to at least 60 (20 per language), drawn from the held-out corpus with a category mix: praise, mild, harsh, refund demand, safety/legal, sarcasm, injection attempt. One JSON file per case.
2. Metrics: (a) structural guardrail pass rate (existing); (b) rubric score 1-5 on tone fit, specificity (addresses the actual complaint), no invented commitment, language/register, safe to post - rated by two judges (a different model family from the drafter, plus GG on a 20-case sample) with agreement reported as kappa; (c) a hard-fail rate for fabricated commitments on a seeded set with a vernacular-aware detector.
3. Statistics: n=60 gives a binomial 95% half-width near 12 points at a 50% rate, so report intervals and only claim differences above the noise floor (`eval/results/noise_floor_baseline.json` pattern); cassette-record once per prompt version, replay in CI.
4. Gate: add `eval.yml` job with thresholds set from the first measured run, not before; bump `REPLY_PROMPT_VERSION` and document in `PROMPTS.md` on any prompt change.
5. Quota: recording 60 drafts is about 105K tokens on the large pool; split across two days under the 100K working budget, never during a customer-heavy window.

Effort to "real" feature: eval + vernacular guardrails + per-org rate limit + cost row: about 4-6 days; no new UI required.

## Provenance and open items

- Repo facts: `origin/main` a08fff0; files cited inline.
- External facts fetched 2026-10-07: Flipkart seller API docs, Amazon Customer Feedback API guide, Resend pricing (fetch tool); others are search-result summaries and are marked BELIEVED.
- Not verified: prod env vars for Shopify, Cloud Scheduler job states, Resend live sender, Groq Developer-plan limits, Amazon.in terms text, OpenRouter list price.
