# Q5: Judge.me connector feasibility, and GDPR/uninstall schema proposal

Date: 2026-10-08. Research and docs only. Nothing here was applied, built, or called with
credentials. PRs #291-#293 were read (`gh pr diff`) and not modified. Base: `origin/main` bae3014.

Label key: VERIFIED = I fetched the page this session and the fetched text said it (URL given).
SNIPPET = seen only in a search-result summary (the URL is listed; re-check before external use).
BELIEVED = my inference, not established by a source. UNKNOWN = looked, could not establish.

Fetch limits worth knowing: `judge.me/api/docs` is rendered client-side, so the fetch returned
only a heading and no endpoint detail; `judge.me/pricing` returned HTTP 403. Plan-level and
webhook claims for Judge.me therefore rest on search summaries, not on a page I read.

## Q5a (1). Judge.me

| Question | Finding | Label |
|---|---|---|
| Does Judge.me's own API give a third-party app review READ access? | Yes, via a per-merchant token. `GET /reviews` takes `api_token`, `shop_domain`, `per_page`, `page`, `product_id`, `rating`, `published`, `reviewer_id`, `reviewer_email`. | VERIFIED https://judge.me/help/en/articles/8409180-using-judge-me-api |
| Auth | Two credentials: `shop_domain` (myshopify.com form) and `api_token`. Merchant finds them in Judge.me admin, Settings > Integrations, "View API tokens". | VERIFIED (same URL) |
| Token types | Public token: GET on the widget API only, for public JS. Private token: "read/write access to your data", server-side only. There is no read-only private token. | VERIFIED (same URL) |
| Token rotation | "You can't regenerate your Private API token yourself in the Judge.me admin"; no self-serve option. A leaked token cannot be rotated by the merchant without contacting Judge.me. | VERIFIED (same URL) |
| OAuth / partner program | Not found. A third-party summary says auth is a static per-shop `api_token` query parameter with no org-level or multi-shop token. I found no OAuth flow and no partner/"Awesome partnership" program page. | SNIPPET https://www.stitchflow.com/user-management/judge.me/api ; absence of OAuth is weak evidence (rule 101a), not "confirmed absent" |
| Plans that include API | The help article does not state plan requirements. Webhooks: Awesome plan only. Whether the REST API itself is on the free plan: not established. | Webhooks SNIPPET https://judge.me/help/en/articles/8630762 and https://help.trueloyal.com/docs/judgeme-integration ; API-on-free UNKNOWN. Pricing page 403 |
| Rate limits | None documented on the help page. | VERIFIED absence on that page only; the API docs page was not readable |
| Pagination | `per_page` max 100, `page` number. | VERIFIED (help URL) |
| Fields returned | Documented in the fetch: `pictures` (URLs and hidden flag), `has_published_pictures`, `has_published_videos`. Not included: video URLs and review replies. Rating, body, title, reviewer, created_at, product id, verified flag, language: the fetch summary did not list them. | VERIFIED for the three listed and the two exclusions; the rest BELIEVED present (the widely used review object has them) and must be confirmed against a real response before mapping |
| Webhooks for new reviews | Yes, Awesome plan only. Events `review/created`, `review/updated`, `review/published`, `review/unpublished`; configured in Settings > Integrations > Webhooks or via API. Signature scheme and retry behaviour: UNKNOWN. | SNIPPET (the two URLs above) |
| ToS limits on third-party processing | No Judge.me terms page was fetched; search returned nothing usable. Merchant-owned review data pulled with the merchant's own token is the normal pattern, but nothing I read says the terms permit a third party to process it. | UNKNOWN; needs a read of Judge.me's terms and a decision before building |

Other facts from this repo's earlier research (docs/research/shopify-icp-evidence.md): the public
storefront widget JSON returns reviews without auth, but it is a render endpoint, not a sanctioned
API; do not build on it.

## Q5a (2). The other apps, briefly, and a ranking

| App | Own API | Auth | Webhooks / plan | Label |
|---|---|---|---|---|
| Yotpo | Reviews API (V1 supported, V3 rolling out for storefront reviews) | app_key/secret, utoken, or OAuth | Docs state rate limits, webhooks and plan-based restrictions exist; specifics not read | VERIFIED headline only: https://apidocs.yotpo.com/reference/welcome ; rest SNIPPET |
| Loox | Two APIs: Storefront (no customer PII) and Merchant (full data, authenticated) | Merchant API requires authentication (mechanism not read) | Reviews API and webhooks on the Convert and Unlimited plans; webhooks on new submission, approval, reply | SNIPPET https://help.loox.io/support/solutions/articles/501000356871-loox-reviews-api-and-webhooks |
| Stamped.io | Reviews APIs under "Merchant APIs"; two API versions | Earlier research: HTTP Basic with public + private key | Not read | VERIFIED index only: https://developers.stamped.io/ ; auth SNIPPET |
| Okendo | Merchant API, webhooks section exists | Not read | Not read | VERIFIED index only: https://docs.okendo.io/ |
| Shopify Product Reviews (native) | None; discontinued 2024-05-06, CSV export only | n/a | n/a | From shopify-icp-evidence.md (Koala fetch VERIFIED there) |
| Shopify metaobject (`product_review`) | Admin API metaobjects, restricted definition for approved review apps | Shopify OAuth | `metaobjects/create` webhook | shopify.dev, VERIFIED in shopify-icp-evidence.md; whether a non-review app may read it is still UNTESTED |

Ranking for Indian Shopify D2C, by evidence in shopify-icp-evidence.md (Koala tracked stores,
2026-08-24: Judge.me 2,520, native Product Reviews 804, Loox 768, Yotpo 117, Stamped 92,
Okendo 78; own homepage sweep of 45 candidate Indian domains: 17 contain `judge.me` strings, 2
Loox, 2 Yotpo):

1. Judge.me. Largest by every source there. Sampling caveats are in that doc (my own candidate list, string match is not proof the widget is live).
2. Loox. Second by Koala count and present in the Indian sweep; API gated to paid plans.
3. Yotpo. Few Indian hits, skews Plus and enterprise; best-documented API (OAuth available).
4. Stamped, then Okendo. Smallest counts.
The discontinued native app is still large (804) but has no API; its reviews can only arrive by CSV.

## Q5a (3). What a Judge.me connector costs in this repo

Inspected: `gh pr diff` for #291 (OAuth begin/callback, scopes, config), #292 (backfill via durable
queue: `app/core/ingestion/shopify_backfill.py`, `shopify_source.py`, `storage_pg.py`), #293
(Connect Shopify page and callback route), the existing `supabase/migrations/20260622000001_shopify_installations.sql`,
and `app/api/webhooks/shopify.py` (`encrypt_token` / `_decrypt_token`, MultiFernet key rotation).
Note: the `shopify_installations` table is already on main (migration 20260622000001), not added by #291.

Merchant flow: the merchant copies the private token and `shop_domain` from Judge.me admin and
pastes them into our Connect page. No Shopify App Store review, no Shopify OAuth, no Shopify
Partner app is needed for this path. Judge.me needs no review of us either (no partner program
found). Cost: the merchant hands us a read/write token that they cannot rotate.

Component list (engineer-days are ESTIMATES, not measurements; one engineer, includes tests):

| Component | Reusable from #291-#293 / main | New work | Est. days |
|---|---|---|---|
| Judge.me review source (`GET /reviews`, page loop to max 100, map to `ReviewRow`, honour the backfill cap) | `ReviewRow` and the source shape in `shopify_source.py` (BELIEVED source-agnostic; I did not verify the protocol) | HTTP client, field mapping verified against a real response, timeout/backoff per rule 108 | 1.5 |
| Token store | `encrypt_token` / `_decrypt_token` with MultiFernet rotation (VERIFIED in webhooks/shopify.py) | New table `judgeme_connections` (org_id, shop_domain unique, token_enc, connected_at, revoked_at, last_synced_at) with RLS and an undo case; do not overload `shopify_installations` (its token column is a Shopify OAuth token) | 1.0 |
| Connect endpoints (paste, validate by one `per_page=1` call, store, status, disconnect) | JWT to org resolution pattern and typed errors from #291 (`_bearer`, `_error`, `/status` shape) | The endpoints; no hmac/state/redirect | 1.0 |
| Backfill | `plan_rows`, `enqueue_backfill`, `existing_input_hashes_pg`, durable queue staging, dedupe, cap (#292) | Wire the new source in | 0.5 |
| Ongoing sync | Cloud Scheduler tick pattern (ingest-tick exists in prod per project notes) | Poll job: `published=true`, newest first, stop at last-seen; per-connection cursor | 1.5 |
| Webhook intake (optional, Awesome plan merchants only) | Webhook route skeleton | Signature verification scheme UNKNOWN: confirm before estimating; skip in v1 and poll | 1.0, only if wanted |
| Web: paste-token form, loading/empty/error states, screenshots | `Shopify.tsx` page layout, `api.ts`, nav entry (#293) | New page and validation copy that tells the merchant what the private token can do | 1.0 |
| Docs, runbook, ToS check, secret-handling review | `docs/runbooks/shopify-first-install.md` as a template | Judge.me runbook, written decision on the terms question | 0.5 |

Total about 7 days without the webhook, about 8 with it. Everything that is new in the data model
is one table plus one migration. These are estimates; none were measured.

What must be new: the source client, the connections table and migration (+ undo case in
`tests/integration/test_push_postconditions.py`), the connect/validate endpoints, the poll job.

Does it replace the metaobject path in #291-#293? Recommendation (BELIEVED, judgment): no, it
complements it. Reasons: the metaobject read is still untested on a dev store, so I cannot say
it is dead; Judge.me covers the largest app; but the metaobject path covers any participating app
with one OAuth install, which scales better if Shopify permits the read. Decision input GG needs:
the dev-store test of the restricted metaobject read.

What remains reusable from #291-#293 if the metaobject path is dropped: the Fernet token helpers,
the whole backfill module (planning, dedupe, cap, durable-queue staging), the typed-error and
`/status` conventions, the Connect page shell and `api.ts` plumbing. What becomes dead code:
OAuth begin/callback, state/hmac verification, the scopes constant, the `shopify_app_url` config,
the SPA callback route, `ShopifyCallback.tsx`.

Risks specific to Judge.me, all from the table above: no read-only token, no merchant-side
rotation, plan-gated webhooks, unverified API-on-free-plan, ToS unread.

## Q5c. Proposed schema for uninstall and mandatory GDPR webhooks (TEXT ONLY, NOT APPLIED)

Requirement basis (VERIFIED https://shopify.dev/docs/apps/build/privacy-law-compliance):
App Store apps must subscribe to `customers/data_request`, `customers/redact`, `shop/redact`;
respond 200-series, 401 on bad HMAC; data requests and customer redactions must be completed
within 30 days; `shop/redact` is sent 48 hours after uninstall; `shop/redact` carries only
`shop_id` and `shop_domain`; the other two carry shop_id, shop_domain, customer details and order
ids. `app/uninstalled` is a normal webhook topic (not on that page; its behaviour is BELIEVED
from general Shopify knowledge): sent at uninstall, after which the access token is invalid.

Conventions read and followed:
- Migrations are plain SQL in `supabase/migrations/<timestamp>_<name>.sql`, additive and
  idempotent (`IF NOT EXISTS`, `DROP POLICY IF EXISTS` then create), with a header explaining why
  and a rollback note (as in 20260622000001 and 20260920000001).
- Each migration carries `-- @postcondition:` blocks (grammar in `supabase/postconditions.py`,
  verified by `supabase/push.py`), and per the "every migration gets an undo case" rule
  (#237) each migration must have at least one entry in `UNDO_CASES` in
  `tests/integration/test_push_postconditions.py`: SQL that undoes the effect so the postcondition
  goes FALSE. A per-migration coverage test fails otherwise. The undo SQL for the proposal is
  listed below as that test input; it is not a DDL "down" for production.
- RLS: tenant tables use `org_id = public.current_org_id()` select policies for `authenticated`
  and an `anon` deny policy. Pre-tenant writes (the webhook arrives HMAC-verified but with no JWT
  and no org context) follow the `leads` pattern: revoke from PUBLIC/anon/authenticated, narrow
  column grants and explicit policies for `review_iq_app` only, no `service_role` privileges.
- `scripts/check_migrations_no_bypassrls_grant.py` rejects any `BYPASSRLS` grant in
  `supabase/migrations/`; the proposal contains none. The app role stays NOBYPASSRLS (ADR 0006).
- Any new function that touches an `org_id`-less table needs an entry in
  `scripts/check_undocumented_pg_connects.py` ALLOWLIST with a reason (the webhook handlers are
  pre-tenant).

Minimum needed, three pieces:
1. `shopify_installations`: record the shop id from compliance payloads, make uninstall wipe the
   token, and record when uninstall and shop redaction happened. Existing `revoked_at` already
   means "no longer active"; reuse it for uninstall rather than adding a duplicate column.
2. `shopify_webhook_events`: idempotency and audit. Shopify retries; `X-Shopify-Webhook-Id` is
   the dedupe key. Stores no payload (the compliance payloads contain customer PII).
3. `shopify_redaction_requests`: the ledger that proves each of the three request kinds was
   received, due, and completed within the deadline.

### Proposed up (file would be `supabase/migrations/<next-timestamp>_shopify_uninstall_and_compliance.sql`)

```sql
-- 1. shopify_installations: uninstall + shop redaction support.
ALTER TABLE public.shopify_installations
    ADD COLUMN IF NOT EXISTS shopify_shop_id bigint,
    ADD COLUMN IF NOT EXISTS redacted_at timestamptz;

-- On app/uninstalled the token is invalid and must not be retained: allow it to be NULL, but
-- only on a revoked row, so an active install can never lose its token.
ALTER TABLE public.shopify_installations
    ALTER COLUMN access_token_enc DROP NOT NULL;
ALTER TABLE public.shopify_installations
    DROP CONSTRAINT IF EXISTS shopify_inst_active_has_token;
ALTER TABLE public.shopify_installations
    ADD CONSTRAINT shopify_inst_active_has_token
    CHECK (revoked_at IS NOT NULL OR access_token_enc IS NOT NULL);

-- 2. Webhook idempotency + audit. No payload is stored (compliance payloads carry customer PII).
CREATE TABLE IF NOT EXISTS public.shopify_webhook_events (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    -- X-Shopify-Webhook-Id; UNIQUE makes a retried delivery a no-op insert.
    webhook_id   text        NOT NULL UNIQUE CHECK (char_length(webhook_id) BETWEEN 1 AND 128),
    topic        text        NOT NULL CHECK (char_length(topic) BETWEEN 1 AND 64),
    shop_domain  text        NOT NULL CHECK (shop_domain ~ '^[a-zA-Z0-9][a-zA-Z0-9-]*\.myshopify\.com$'),
    received_at  timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    status       text        NOT NULL DEFAULT 'received'
                 CHECK (status IN ('received', 'processed', 'failed', 'ignored'))
);
CREATE INDEX IF NOT EXISTS idx_shopify_webhook_events_shop
    ON public.shopify_webhook_events (shop_domain, received_at DESC);

-- 3. Redaction request ledger (30-day clock for customer requests; shop/redact arrives
--    48 hours after uninstall).
CREATE TABLE IF NOT EXISTS public.shopify_redaction_requests (
    id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    webhook_event_id    uuid        NOT NULL REFERENCES public.shopify_webhook_events(id),
    kind                text        NOT NULL
                        CHECK (kind IN ('customer_data_request', 'customer_redact', 'shop_redact')),
    shop_domain         text        NOT NULL,
    -- NULL when the shop was never linked to an org, or the org is gone (SET NULL keeps the audit row).
    org_id              uuid        REFERENCES public.organizations(id) ON DELETE SET NULL,
    -- Shopify numeric ids only; no email, name or address is stored in the ledger.
    shopify_customer_id bigint,
    received_at         timestamptz NOT NULL DEFAULT now(),
    due_at              timestamptz NOT NULL,
    status              text        NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'completed', 'nothing_to_do', 'failed')),
    completed_at        timestamptz,
    CONSTRAINT shopify_redaction_customer_id_required
        CHECK (kind = 'shop_redact' OR shopify_customer_id IS NOT NULL),
    CONSTRAINT shopify_redaction_completed_has_timestamp
        CHECK ((status IN ('completed', 'nothing_to_do')) = (completed_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_shopify_redaction_open
    ON public.shopify_redaction_requests (due_at) WHERE status = 'pending';

-- RLS + grants: pre-tenant tables, `leads` pattern. review_iq_app only; no authenticated/anon/service_role.
ALTER TABLE public.shopify_webhook_events     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.shopify_redaction_requests ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.shopify_webhook_events     FROM PUBLIC, anon, authenticated;
REVOKE ALL ON public.shopify_redaction_requests FROM PUBLIC, anon, authenticated;

GRANT INSERT, SELECT ON public.shopify_webhook_events TO review_iq_app;
GRANT UPDATE (processed_at, status) ON public.shopify_webhook_events TO review_iq_app;
GRANT INSERT, SELECT ON public.shopify_redaction_requests TO review_iq_app;
GRANT UPDATE (status, completed_at) ON public.shopify_redaction_requests TO review_iq_app;
-- No DELETE on either: the ledger is the proof of compliance.

DROP POLICY IF EXISTS "shopify_webhook_events_app_all" ON public.shopify_webhook_events;
CREATE POLICY "shopify_webhook_events_app_all" ON public.shopify_webhook_events
    FOR ALL TO review_iq_app USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "shopify_redaction_requests_app_all" ON public.shopify_redaction_requests;
CREATE POLICY "shopify_redaction_requests_app_all" ON public.shopify_redaction_requests
    FOR ALL TO review_iq_app USING (true) WITH CHECK (true);

-- review_iq_app must also be able to mark an install revoked and wipe its token (webhook is
-- pre-tenant, so it cannot go through _set_tenant). Narrow column grant, no new row access:
GRANT UPDATE (revoked_at, access_token_enc, redacted_at, shopify_shop_id)
    ON public.shopify_installations TO review_iq_app;
-- Open design question, not decided here: shopify_installations has no review_iq_app policy
-- today (it is read via the SECURITY DEFINER resolve_org_for_shopify_shop). Either add a narrow
-- UPDATE policy for review_iq_app, or add a SECURITY DEFINER function
-- public.revoke_shopify_installation(shop_domain text) that does only the update. The function
-- is the smaller surface and is the BELIEVED better choice; it follows the
-- 20260801000002_tenant_resolvers_auth_signup.sql pattern.
```

Postconditions the real migration would carry (names only; SQL follows the existing grammar):
`shopify_installations_uninstall_columns`, `shopify_webhook_events_columns_and_unique`,
`shopify_redaction_requests_columns_and_checks`, `shopify_compliance_tables_rls_app_only`
(RLS on, policies are review_iq_app only, `authenticated`/`anon`/`service_role` hold no
privileges).

### Proposed down (rollback, for the migration header; not for CI)

```sql
-- Order matters: the ledger references the events table.
DROP TABLE IF EXISTS public.shopify_redaction_requests;
DROP TABLE IF EXISTS public.shopify_webhook_events;
REVOKE UPDATE (revoked_at, access_token_enc, redacted_at, shopify_shop_id)
    ON public.shopify_installations FROM review_iq_app;
ALTER TABLE public.shopify_installations
    DROP CONSTRAINT IF EXISTS shopify_inst_active_has_token;
-- Restoring NOT NULL fails if any revoked row already has a NULL token. Backfill first:
-- UPDATE public.shopify_installations SET access_token_enc = '' WHERE access_token_enc IS NULL;
-- (an empty string is not a valid Fernet token and is never decrypted for a revoked row.)
ALTER TABLE public.shopify_installations ALTER COLUMN access_token_enc SET NOT NULL;
ALTER TABLE public.shopify_installations
    DROP COLUMN IF EXISTS redacted_at,
    DROP COLUMN IF EXISTS shopify_shop_id;
```
Dropping the two tables destroys the compliance ledger; that makes the down a data-deletion
step. It should only run before any real compliance webhook has been recorded.

### Undo cases to add to `UNDO_CASES` with the real migration (SQL that must flip each postcondition to FALSE)

```text
(<file>, "shopify_installations_uninstall_columns",
    "ALTER TABLE public.shopify_installations DROP CONSTRAINT shopify_inst_active_has_token")
(<file>, "shopify_webhook_events_columns_and_unique",
    "ALTER TABLE public.shopify_webhook_events DROP CONSTRAINT shopify_webhook_events_webhook_id_key")
(<file>, "shopify_redaction_requests_columns_and_checks",
    "ALTER TABLE public.shopify_redaction_requests DROP CONSTRAINT shopify_redaction_completed_has_timestamp")
(<file>, "shopify_compliance_tables_rls_app_only",
    "GRANT SELECT ON public.shopify_redaction_requests TO authenticated")
```

### What this proposal does not decide (needs GG or a follow-up, in order of importance)

1. What customer data we actually hold for `customers/redact` and `customers/data_request`. If
   we store only review text with no reviewer identity, the honest handling is
   `nothing_to_do` plus a note; if reviewer names or order ids reach `extractions.raw_text` or
   metadata, a deletion path keyed on `shopify_customer_id` is needed. I did not audit the
   extraction schema for this (BELIEVED we store review text, UNVERIFIED about identifiers).
2. `shop/redact` semantics: delete the org's Shopify-sourced data, or only the installation row?
   Deleting data is irreversible and needs an explicit decision (rule 54a).
3. The `review_iq_app` update path for `shopify_installations` (policy versus SECURITY DEFINER
   function), noted inline above.
4. Whether to submit to the App Store at all; these webhooks are required only for that path.
   A Judge.me paste-token connector needs none of this schema.

STOP: no DDL was added under `supabase/migrations/`, nothing was applied, and no application code
was changed. Implementation is gated on GG's go-ahead.
