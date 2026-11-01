# Shopify first real install: runbook

Date: 2026-10-08. Code state: `origin/main` at `52cc781` plus the stacked draft PRs named below.
Labels: VERIFIED = read in this repo or on a cited Shopify page today; BELIEVED = inference, with
how to confirm. Nothing here was run against a real Shopify store: no Partner account, dev store or
Shopify CLI exists on this machine (checked: `shopify` not on PATH).

## 0. Verified state of `main` (before the PRs)

| Claim | Result |
|---|---|
| `shopify_installations` table, `/auth/shopify/begin`, `/auth/shopify/callback`, `/webhooks/shopify/reviews`, HMAC + Fernet | VERIFIED present (`app/api/shopify_auth.py`, `app/api/webhooks/shopify.py`) |
| No `web/` frontend | VERIFIED: `git grep -i shopify -- web` is empty |
| Shopify env vars absent from deploy config | VERIFIED: nothing in `.github`, `cloudbuild.yaml`, `ops/runbooks/cloud-run-deploy.md` |
| `ShopifySource.fetch_reviews` never called | VERIFIED: only the webhook imports `_node_to_review_row` |
| Begin builds a backend redirect, callback expects an SPA POST | VERIFIED (begin used `{webhook_base_url}/auth/shopify/callback`) |
| OAuth scope asked `write_product_reviews` (and `read_customers`) | VERIFIED; connector only reads |
| No uninstall / GDPR webhooks | VERIFIED: no `uninstalled`/`data_request`/`redact` in `app/` |
| Genuine Shopify callbacks carry extra signed params (`host`) the old body model dropped | BELIEVED from Shopify's documented callback format; fixed defensively (extra params kept in the HMAC) |

## 1. What Claude can do (code, wiring, backfill)

| Item | Status | Where |
|---|---|---|
| Reconcile redirect: `redirect_uri = {SHOPIFY_APP_URL}/shopify/callback` (SPA route; only the SPA holds the seller JWT, org_id comes from that JWT only), user-bound state, all params in HMAC | Built, draft PR `feat/s19-shopify-connect-flow` | `app/api/shopify_auth.py` |
| Read-only scopes `read_metaobjects,read_products` | Built, same PR | `SHOPIFY_SCOPES` |
| `SHOPIFY_ENABLED` switch, off by default; enabled + missing setting = app refuses to start | Built, same PR | `app/core/config.py` |
| `GET /auth/shopify/status` | Built, same PR | |
| Post-install backfill through the durable queue (cap 500, dedupe by text hash, no inline LLM) | Built, draft PR `feat/s19-shopify-backfill` (stacked) | `app/core/ingestion/shopify_backfill.py` |
| Connect page + `/shopify/callback` SPA route | Built, draft PR `feat/s19-shopify-web-connect` (stacked on the first) | `web/src/pages/Shopify*.tsx` |
| `app/uninstalled` webhook (set `revoked_at`) | NOT built. Needs a new SECURITY DEFINER function (migration, `authenticated` has SELECT only on the table) | to do |
| `customers/data_request`, `customers/redact`, `shop/redact` endpoint(s) with HMAC, 401 on bad HMAC, 200 on success | NOT built. `shop/redact` needs a migration (delete installation + imported data); the two customer topics are ack-only if we hold no customer-keyed data (BELIEVED: we store review text, not Shopify customer IDs; get a legal read) | to do |
| Metaobject webhook is registered without a `type:product_review` filter | Known; the handler drops non-review payloads. Move to GraphQL `webhookSubscriptionCreate` with a filter later | to do |
| Verify the metaobject `type` string (`product_review`) and `reverse: true` ordering against a real store | Needs a dev store | blocked on GG |

## 2. What needs GG (cannot be done by Claude)

1. **Shopify Partner account** (partners.shopify.com). Creating a dev store needs it.
2. **Create the app** (Partner dashboard, or `shopify.app.toml` via Shopify CLI):
   - App URL: `https://app.samidhareviews.xyz` (the web origin; BELIEVED from the project's Vercel setup, confirm).
   - Allowed redirection URL: `https://app.samidhareviews.xyz/shopify/callback` (exactly `SHOPIFY_APP_URL` + `/shopify/callback`).
   - Scopes (read-only, least privilege): `read_metaobjects`, `read_products`. Do NOT add `write_*` or `read_customers`.
   - Webhooks (API version `2024-10`, matches `SHOPIFY_API_VERSION`):
     - `metaobjects/create` is registered per shop by the callback at `https://api.samidhareviews.xyz/webhooks/shopify/reviews`.
     - `app/uninstalled` -> `https://api.samidhareviews.xyz/webhooks/shopify/app-uninstalled` (endpoint not built yet).
     - Mandatory compliance topics `customers/data_request`, `customers/redact`, `shop/redact` -> `https://api.samidhareviews.xyz/webhooks/shopify/gdpr` (endpoint not built yet). VERIFIED (shopify.dev, privacy-law-compliance, fetched 2026-10-08): required for any app distributed through the App Store (not custom apps); bad HMAC must return 401, success 200-series; work completed within 30 days; `shop/redact` arrives 48 hours after uninstall; configured in `shopify.app.toml` `[webhooks]` `compliance_topics` + `uri`.
3. **Secrets in Secret Manager** (project `reviewiq-prod-260813`; do not create from a session). Names are proposals following the repo's lower-kebab style; the runtime service account must get `roles/secretmanager.secretAccessor` per secret:

   | Env var | Secret Manager secret | Kind |
   |---|---|---|
   | `SHOPIFY_CLIENT_ID` | `shopify-client-id` | secret-backed (not sensitive, kept uniform) |
   | `SHOPIFY_CLIENT_SECRET` | `shopify-client-secret` | secret |
   | `SHOPIFY_TOKEN_ENCRYPTION_KEY` | `shopify-token-encryption-key` | secret; generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`; rotation: `ops/runbooks/secret-rotation.md` |
   | `SHOPIFY_APP_URL` | n/a | plain env var: `https://app.samidhareviews.xyz` |
   | `SHOPIFY_WEBHOOK_BASE_URL` | n/a | plain env var: `https://api.samidhareviews.xyz` |
   | `SHOPIFY_API_VERSION` | n/a | optional, default `2024-10` |
   | `SHOPIFY_ENABLED` | n/a | plain env var, `true` only once everything above exists |

   `SHOPIFY_APP_URL` and `SHOPIFY_WEBHOOK_BASE_URL` must be `https://` with no trailing slash; the app refuses to boot otherwise, and refuses to boot with `SHOPIFY_ENABLED=true` and any of the five required values missing or an invalid Fernet key.
4. **Deploy** from CI per `ops/runbooks/cloud-run-deploy.md`: use `--update-env-vars` / `--update-secrets` (merge), never `--set-*` (replace), or the existing 17 vars are wiped. Deploy sequence: merge PRs, deploy with `SHOPIFY_ENABLED` unset (default off, safe), add secrets, then set `SHOPIFY_ENABLED=true` in a separate revision so the boot check validates the config. Production deploys come from CI on `main`, never from a local tree.
5. **Dev store + a review app** (e.g. Judge.me free) that writes the Standard Product Review metaobject, then run section 3.
6. **Shopify app review / listing**: required to install on stores you do not own beyond dev stores/custom distribution. Calendar time outside our control; needs the compliance endpoints (section 1) first, a privacy policy URL and the app listing copy.
7. Confirm Firebase Hosting rewrite (`api.samidhareviews.xyz`) forwards the raw body and `X-Shopify-*` headers unchanged (BELIEVED fine; HMAC would fail otherwise, so test with a real webhook).

## 3. First-install test sequence (after section 2 steps 1 to 5)

1. `SHOPIFY_ENABLED=true` revision boots; `GET /auth/shopify/status` with a seller JWT returns `{"enabled": true, ...}`.
2. Sign in on the web app, Integrations, enter `<dev-store>.myshopify.com`, Connect Shopify.
3. Shopify consent screen lists only the two read scopes. Approve.
4. Browser lands on `/shopify/callback?...` then `/integrations/shopify?connected=<shop>`. API logs `shopify_auth.install_complete`.
5. A `shopify_installations` row exists for the seller's org (read-only query, no write).
6. Backfill: logs `shopify_backfill.enqueued` (or `nothing_to_enqueue`), `batch_job_rows` drain, reviews appear in the dashboard. Re-run the install: expect `nothing_to_enqueue` (idempotence).
7. Add a review in the review app: webhook `shopify_webhook.processed` appears.
8. Uninstall the app; once the uninstall + `shop/redact` endpoints exist, confirm `revoked_at` is set and data removed.

Stateless-mode orgs are skipped by the backfill (same constraint as CSV ingest, ADR 0024/0025).

## 4. Cost and risk notes

- Backfill ceiling 500 reviews per install: about $0.27 at the measured $0.000534 per extraction (`eval/results/token_cost_measurement_n106.json`), and it drains through the existing Groq limiter, so it competes with other bulk work for the 200K tokens/day pool.
- Merchants only have reviews in the metaobject if their review app syndicates to it; native Shopify reviews were shut down in May 2024 (BELIEVED, prior research in `shopify_source.py`). Expect some connected stores to import zero reviews.
