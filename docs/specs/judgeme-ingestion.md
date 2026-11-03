# Judge.me review ingestion connector: spec and pre-registered evaluation

**Target user:** a Shopify seller who collects reviews with Judge.me and wants them classified in Samidha Reviews without exporting CSVs.
**Pain point:** Judge.me reviews reach Samidha only by manual CSV upload, so the dashboard is stale the day after upload and edits or removals in Judge.me are never reflected.
**Success metric:** after pasting `shop_domain` and a private API token, every published review is ingested exactly once, edits and removals are reflected within the stated lag bounds, and a bad or revoked token is surfaced to the merchant instead of failing silently (thresholds in section 5, all pre-registered before any code).
**Who pays:** the seller, on the existing tiers (retained-mode orgs only); no new price line.

Status: pre-registration. This file is committed before any connector code exists. Date: 2026-10-11.
Supersedes the API facts in `docs/specs/judgeme-connector-scope.md` where they differ (that file stays as the earlier scoping record).

Evidence label used throughout: **VERIFIED** = read on a Judge.me help page by GG's research this session, or read in this repo. **BELIEVED** = third-party or inferred, not confirmed against a real Judge.me store. Every number this connector will report from tests is **SYNTHETIC / CONTRACT-FIXTURE BASED**: it is measured against a fake Judge.me server built from the shape below, never against a real store. A real-store measurement does not exist until section 9 is run.

## 1. Facts table

| # | Fact | Label | Consequence for the design |
|---|---|---|---|
| F1 | Private token is under Settings > Integrations > "View API tokens"; the seller cannot regenerate it (support only) | VERIFIED (judge.me/help/en/articles/8409180) | Disconnect cannot rotate it; we wipe our copy and tell the merchant to contact Judge.me if they believe it leaked |
| F2 | `GET https://api.judge.me/api/v1/reviews` requires `api_token` and `shop_domain`; optional `per_page` (max 100), `page`, `product_id`, `rating`, `published`, `reviewer_id`, `reviewer_email` | VERIFIED (same page) | Client sends only `shop_domain`, `per_page`, `page`; never `product_id`, never reviewer filters |
| F3 | A `product_id` that is not the store's silently returns ALL store reviews | VERIFIED | We never pass `product_id`; a per-product pull is not built |
| F4 | Response omits video URLs and replies; API-created reviews cannot be marked verified or deleted | VERIFIED | Not needed for classification |
| F5 | Help page states no rate-limit numbers and does not say which plan has API access | VERIFIED (absence on that page only) | Back-off design, no assumed limit; plan requirement stays UNKNOWN (section 9) |
| F6 | A Shopify development store gets the Awesome plan free for testing; webhooks are mentioned, event names not listed there; OAuth "strongly recommended" for apps, partner contact partnerships@judge.me | VERIFIED (judge.me/help/en/articles/8278390) | Test path in `docs/runbooks/judgeme-test-store.md`; OAuth is a future decision, not v1 |
| F7 | Review object fields: `id, title, body, rating, product_external_id, reviewer{id,external_id,email,name,phone,...}, source, curated, published, hidden, verified, created_at, updated_at, pictures` | BELIEVED (API Evangelist profile, self-described generated/unverified) | Mapper tolerates absence of every field except `id` and a non-empty text; field names re-confirmed by section 9 |
| F8 | List response is `{current_page, per_page, reviews}` with no total and no next cursor; end of data = short or empty page | BELIEVED | Terminal rule below handles both "short page" and "empty page"; works if `per_page` is not echoed |
| F9 | No server-side `updated_at`/`created_at` filter | BELIEVED | Client-side watermark, overlap window, periodic full reconciliation |
| F10 | List ordering is not documented | UNKNOWN | Default `unknown`: every run is a full scan (correct under any order). Early-stop incrementals only after section 9 confirms an order |
| F11 | Webhook events `review/created, updated, published, unpublished`; no deleted event; HMAC-signed | BELIEVED | v1 polls only; webhooks are out of scope, so deletions need reconciliation |
| F12 | REST API available on the Forever Free plan | BELIEVED (contradicted by nothing, confirmed by nothing) | UNCONFIRMED for the free plan; cheapest free alternative is the dev-store Awesome plan (F6) |
| F13 | 429 under heavy load, limits unpublished | BELIEVED | Retry-After honoured when present, exponential back-off with full jitter otherwise |
| F14 | Token may also be sent as an `X-Api-Token` header | BELIEVED (official page not renderable) | `auth_transport` option, default `header`, falls back to the VERIFIED query parameter at connect time only (section 3.3) |
| F15 | How a revoked token looks | UNKNOWN | Treated as a repeated 401 or 403 on a previously-working installation (section 3.5); recorded response required in section 9 |
| F16 | Unpublished/hidden reviews may or may not appear in the unfiltered list | UNKNOWN | Both handled: a visible item with `published=false` or `hidden=true` is removed at once; an item that is simply absent is removed after the strike rule |

## 2. Scope

In: private-token paste connector, scheduled client-side-incremental pull, edits, deletions, retention-mode respect, loud/typed failures, BFF endpoints (connect, status, disconnect), feature flag, migration FILE, fake Judge.me server with record/replay.
Out: UI (documented request/response shapes only), webhooks, OAuth, replies, photos/videos, per-product pulls, any live call to Judge.me, applying the migration, deploy.

Flag: `ENABLE_JUDGEME_CONNECTOR` (`Settings.enable_judgeme_connector`, default `false`). Off means: BFF endpoints return 404, the internal sweep returns `{"enabled": false}` and does no I/O. Other settings (all inert while the flag is off): `JUDGEME_TOKEN_ENCRYPTION_KEY` (Fernet, comma-separated for rotation, same scheme as the Shopify key), `JUDGEME_SYNC_TRIGGER_TOKEN` (shared-secret header for the sweep, same pattern as `INGEST_TICK_TOKEN`), `JUDGEME_LIST_ORDER` (`unknown` default | `newest_first` | `oldest_first`), `JUDGEME_SYNC_INTERVAL_MINUTES` (T, default 360), `JUDGEME_OVERLAP_HOURS` (W, default 72), `JUDGEME_RECONCILE_INTERVAL_HOURS` (R, default 24), `JUDGEME_BACKFILL_DAYS` (default 90), `JUDGEME_MAX_ENQUEUE_PER_RUN` (default 500).

## 3. Design

### 3.1 Reuse and new pieces

| Piece | Reused | New |
|---|---|---|
| Source shape | `Source` protocol, `ReviewRow` (`source_review_id`, `review_date`) in `app/core/ingestion/base.py` | `JudgeMeSource` (full pull as a `Source`) and a mapper |
| Queue | `create_batch_job_pg`, `enqueue_batch_job_rows_pg`, `ingest-tick` drain, `_run_extraction_v2` (cache by text hash, `ON CONFLICT DO NOTHING`) | none; reviews are staged exactly like CSV rows |
| Token storage | Fernet `encrypt_token` / MultiFernet scheme of `app/api/webhooks/shopify.py` | table `judgeme_installations`, own key setting |
| Tenant access | `_set_tenant` + narrow SECURITY DEFINER functions (BYPASSRLS remediation pattern) | 4 functions, 2 tables |
| Sync | none | client, scan, plan (pure), executor, internal sweep |

### 3.2 Retention mode (ADR 0025)

`batch_job_rows.text` stages review text durably, which is why CSV ingest is rejected for stateless orgs with 409. The connector follows the same rule and fails closed:
- connect: stateless org gets 409 `retention_required` before the token is probed or stored;
- each sync run re-reads `get_org_retention_pg(org_id)` (an org can change mode after connecting). Stateless (or lookup failure) means: no outbound request, no enqueue, no state write, `last_sync_status = skipped_stateless`, and the per-review state rows for that installation are deleted;
- retained: reviews flow through the queue, so retention purge (`extractions` older than the window) applies unchanged.

What is persisted, and only for retained orgs: queue row (text, product, review_date: same as CSV), state row (`review_id`, `content_hash`, `input_hash`, source timestamps, strike counter), installation row (encrypted token, sync metadata, no review content). Not persisted: reviewer id/name/email/phone, pictures, `verified`, `source`, title as a separate field. The rating is read by the mapper but the existing queue does not carry stars (same limitation as CSV); it is part of `content_hash` so a rating-only edit is still detected, but the extraction keeps inferring stars from text.

### 3.3 Client (`JudgeMeClient`)

- Host fixed to `api.judge.me` over HTTPS with certificate verification; the base URL is a constructor argument for tests only and is not exposed in settings.
- `shop_domain` validated with `re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", s)` after `strip().lower()`, BEFORE the first request, at connect and again at every poll (the DB also carries a CHECK). `fullmatch`, not `$`, because `$` accepts a trailing newline.
- Auth: `X-Api-Token` header by default (F14, BELIEVED); `query` transport sends `api_token=` (F2, VERIFIED). Rationale: a query token lands in URLs, proxy logs and `httpx` INFO logs; a header does not. Connect probe: header first; on 401 retry once with the query transport; the transport that worked is stored on the installation (`auth_transport`). A redaction filter strips `api_token=...` from the `httpx` logger either way.
- Per request: connect timeout 5 s, read timeout 20 s. Max 4 attempts per page.
- 429: sleep `Retry-After` (seconds or HTTP-date) if present and <= 120 s, else abort the run with `RateLimited(retry_after)` and the installation `next_attempt_at = now + retry_after`; absent header: `min(30, 2^attempt)` seconds with full jitter. 5xx (500/502/503/504), timeouts and transport errors: same exponential schedule, then raise. Other 4xx: no retry, typed error. 401/403: no client retry, typed `AuthRejected`.
- Page JSON that is not an object, or `reviews` not a list: `MalformedPage`, retried like a 5xx (bounded), then the run fails without deleting anything.
- Terminal rule for a scan: stop at an empty page, or at a short page when the response echoes `per_page` equal to the request; if the page is short and `per_page` is not echoed, fetch one more page to confirm it is empty. A hard cap of 500 pages marks the scan incomplete.

### 3.4 Scan, plan, execute

A **scan** returns `(items, complete, order_violation, malformed_count)`; items are de-duplicated by id keeping the greatest `updated_at`.
- `full`: pages 1..end. `complete = True` only if the terminal rule was reached with no error and under the page cap.
- `incremental` (only when order is configured, section 3.6): `cutoff = watermark - W`, activity time of an item = `max(created_at, updated_at)`. `newest_first`: pages from 1, stop after the first page whose items all have activity < cutoff. `oldest_first`: find the last page by exponential then binary search for the first empty/short page, walk backwards until a page whose items all have activity < cutoff, then read forward to the end. Never `complete`, so it can never cause a deletion.
- Order self-check on every full scan: if a configured order is violated by both `created_at` and `updated_at`, the installation is set to `order_violation` and all later runs are full scans (fail safe).

The **plan** is a pure function of `(scan, state, now, settings)`. Per review id:

| Observed | State | Action |
|---|---|---|
| visible, published | none, inside backfill window | NEW: stage text, record state |
| visible, published | none, older than `JUDGEME_BACKFILL_DAYS` | skip (counted) |
| visible, published | same `content_hash` | unchanged; reset strikes |
| visible, published | different `content_hash` | EDIT: stage new text if `input_hash` differs, delete the old extraction unless another active review shares its `input_hash`, update state |
| visible, `published=false` or `hidden=true` | active | REMOVE now, state `gone` |
| visible, unpublished | none | skip (counted) |
| absent from a COMPLETE full scan | active | strike += 1; at 2 strikes (3 if more than 50% of >= 20 tracked reviews are missing) REMOVE |
| absent, scan incomplete or zero items returned while reviews are tracked | active | no change, run flagged `suspect` |
| visible again | gone | treated as NEW |
| malformed item (not an object, no id, no text) | any | skipped, counted by reason; never aborts the run |

Execute order (crash-safe, idempotent): stage rows through one `create_batch_job_pg` + `enqueue_batch_job_rows_pg` job, then write state, then delete superseded extractions. A crash between stage and state re-stages the same text on the next run; the extraction cache (`ON CONFLICT (org_id, input_hash) DO NOTHING`, cache lookup before LLM) means no duplicate row and no second LLM call.

`content_hash` = sha256 of the canonical JSON of `(title, body, rating, product, published, hidden)`. Text sent to the queue = `title + "\n" + body` (title omitted if empty), truncated to 5000 characters (`ReviewRequest` limit; truncation counted). `review_date` = `created_at` (the original post date, never ingestion time). Product = `product_title` or `product_handle` if present, else the string form of `product_external_id` (F7 BELIEVED; to be confirmed).

### 3.5 Failure handling contract

| Case | Behaviour |
|---|---|
| Bad token at connect (401/403 on the probe) | 422 `judgeme_bad_token`, nothing stored |
| Token rejected at poll (401/403, repeated once immediately) | installation marked revoked (`revoked_at`, reason `token_rejected`, ciphertext wiped), polling stops, status endpoint shows `needs_action: reconnect`; never retried |
| 404 "shop not found" shape | same path, reason `shop_not_found` |
| 429 with Retry-After <= 120 s | sleeps that long, continues, counted |
| 429 with larger Retry-After | run aborted, `next_attempt_at` set, no data change |
| 429 without header | exponential back-off with full jitter, bounded attempts |
| 5xx / timeout / transport error | bounded retries, then run fails with `last_sync_status = error`, `consecutive_failures += 1`; at 10 consecutive failures the installation stops being picked up until a manual reconnect |
| Malformed page | bounded retry, then run fails, no deletions |
| Malformed item | skip item, count, continue |

### 3.6 Ordering assumption (what we test, what a real store must confirm)

The assumption under test is only used to enable early-stop incrementals: **A-ORDER: the list is monotonic by `created_at` or by `updated_at`, newest first or oldest first, stable across consecutive pages within a run.** `unknown` (default) assumes nothing. Real-store check: section 9, R2.

### 3.7 Lag bounds (to be demonstrated by test, stated here first)

Let T = sync interval, R = reconcile interval, W = overlap window.
- New review: ingested by the first run that starts after it becomes visible: lag <= T (any order mode), provided its activity time is within W of the watermark.
- Edit: `unknown`: <= T. Known order: <= T if its updated activity falls inside the scanned head (always true under updated-time ordering), otherwise <= R + T (next full scan).
- Observed unpublish/hide: same as edit.
- Deletion (absence): <= 2 full scans: `unknown` 2T, known order 2R + T. There is no delete webhook (F11), so this is a floor, not a tuning choice.
- Late publish with moderation delay d > W under known order: not seen by incrementals; seen by the next full scan (<= R + T).

## 4. Threat model

| Threat | Control | Test |
|---|---|---|
| SSRF through `shop_domain` | host fixed; strict `fullmatch` allow-list before any request; lowercase normalisation; DB CHECK | table of hostile values (`evil.com`, `a.myshopify.com.evil.com`, `x.myshopify.com\n`, `user@host`, port, IP, unicode, empty) all rejected with zero requests made |
| Token at rest | Fernet (MultiFernet rotation), own key; table readable only through RLS by org; token never returned by any endpoint; wiped on revoke and disconnect | ciphertext differs from plaintext; status payload has no token field; wipe test |
| Token in logs/errors | redaction filter; no `raise_for_status` (its message contains the URL); structured logs carry shop and counts only | every failure test asserts the token string is absent from captured logs and exception text |
| PII | mapper drops `reviewer` and everything outside the persisted set at the boundary | fixture reviews contain emails/phones/names; asserted absent from staged rows and state |
| Cross-tenant | `org_id` from the verified JWT only; `UNIQUE(shop_domain)`; connecting a shop owned by another active org is rejected (the Shopify upsert lets a later caller overwrite; this one does not) | unit test on the storage contract; RLS integration test written, runs only against a database |
| Replay / forged input | v1 has no inbound webhook; outbound TLS only; the sweep endpoint uses `hmac.compare_digest` on a shared secret | token test |
| Untrusted review text | unchanged downstream path (injection controls, grounding) | existing suite |
| Runaway cost | `JUDGEME_MAX_ENQUEUE_PER_RUN`, 500-page cap, backfill window, bounded retries | caps tested |
| Mass false deletion | strike rule, empty-scan guard, incomplete-scan guard | injected glitch tests, metric M3b |
| Token power | private token is read/write at Judge.me (F1); UI copy must say what we do with it (UI out of scope, noted for the UI PR) | n/a |
| Judge.me ToS | whether a third party may process the data with the merchant's own token is UNREAD (q5 doc) | open item for GG before any real merchant |

## 5. Pre-registered metrics, thresholds and decision rules

All measured on the fake server (`tests/support/judgeme_fake.py`), seed 42, deterministic, simulated clock. They are correctness checks on constructed cases, not rates on real data; "100 percent" therefore means every constructed case passes, and n is the number of constructed cases, reported with each result.

| ID | Metric | Definition | Pass threshold | Cases |
|---|---|---|---|---|
| M1 | Completeness | reviews ingested / reviews published at source at scan start | 100 percent in every case | source sizes 0, 1, 99, 100, 101, 200 (exact multiple, empty last page), 250; per_page echoed and not echoed; both list orders; source grows between pages; page shift from a mid-scan insert |
| M2 | Idempotency | duplicate staged rows per (review id, content version) after 5 consecutive runs; staged rows in runs 2-5 | 0 duplicates; 0 rows staged in runs 2-5 | unknown and each known order; crash between stage and state |
| M3a | Edit/delete correctness | source edits and removals reflected in plan/state after the scan the lag bound allows | 100 percent; lag within section 3.7 bounds | edit, unpublish, hide, delete, re-publish, duplicate-text reviews |
| M3b | False deletions | reviews removed that are still published at source | 0 | empty page-1 glitch, truncated scan, 5xx mid-scan, mid-scan shift under deletion, page cap hit |
| M4 | Freshness | p95 and max of (ingest time - first visible time) on a simulated clock, 200 reviews over 14 days, T = 6 h | p95 <= T and max <= T for creates; reported separately: edits and late publishes | unknown order; newest_first; oldest_first |
| M5 | Failure handling | each case yields the contract in 3.5 | 1 test per case, all pass | bad token, revoked token, 404 shop, 429 with/without Retry-After, 429 over cap, 5xx, timeout, malformed page, malformed item |
| M6 | Privacy | occurrences of fixture email/phone/name/reviewer id in staged rows, state rows, logs | 0 | all scenarios |
| M7 | Retention | writes (stage, state, outbound request) for a stateless org | 0 | connect and sweep |
| M8 | Secret hygiene | occurrences of the token string in logs, exception text, endpoint payloads | 0 | all M5 cases |

Decision rules:
1. Any threshold missed on the fixtures: fix the code, not the threshold; a threshold may be changed only by a new commit to this file that states why, before re-measuring.
2. The flag stays off until M1-M8 pass on fixtures AND section 9 R1-R8 have been run on a real store and recorded.
3. `JUDGEME_LIST_ORDER` changes from `unknown` only if R2 shows the same monotonic order in 3 recordings taken on different days; otherwise it stays `unknown`.
4. If R3 shows any 429 below 20 requests/minute, reduce page size or add inter-page delay before enabling.
5. If R5 shows unpublished reviews are not listed, deletion by absence is the only removal path; its lag bound (2T) is then user-visible and must be stated in the UI copy.

## 6. BFF endpoint contract (UI out of scope)

All under `/bff/judgeme`, authenticated by `require_session` (org from the JWT only), 404 when the flag is off. Errors use `{"detail": {"code": "<machine_code>", "message": "<human text>"}}`.

- `POST /bff/judgeme/connect` body `{"shop_domain": "store.myshopify.com", "api_token": "..."}` -> `201 {"status": "connected", "shop_domain": "store.myshopify.com"}`. Errors: 409 `retention_required`; 409 `shop_connected_elsewhere`; 422 `invalid_shop_domain`; 422 `judgeme_bad_token`; 429 `judgeme_rate_limited` (+ `retry_after`); 502 `judgeme_unreachable`.
- `GET /bff/judgeme/status` -> `200 {"status": "never_connected|active|revoked|disconnected|paused_stateless", "shop_domain": str|null, "last_sync_at": iso|null, "last_sync_status": str|null, "last_error_code": str|null, "reviews_tracked": int, "needs_action": "reconnect|change_retention_mode|null"}`. No token, no internal ids.
- `DELETE /bff/judgeme/connection` -> `204`. Wipes the ciphertext, sets `revoked_at` with reason `disconnected`, deletes the state rows. Existing extractions stay (use the existing purge endpoint to remove them).
- Internal: `POST /internal/judgeme/sync` header `X-Judgeme-Sync-Token`, runs due installations, returns counts.

## 7. Rollout

1. Merge the stacked PRs with the flag off (no behaviour change; no table is read while off).
2. GG decides when to apply the migration FILE (nothing in these PRs applies it). Until applied, with the flag off nothing breaks; with the flag on, connect returns a database error.
3. GG sets `JUDGEME_TOKEN_ENCRYPTION_KEY` and `JUDGEME_SYNC_TRIGGER_TOKEN` in Secret Manager.
4. Section 9 on the test store; decisions 2-5 above.
5. Enable the flag for staging, then production; create the Scheduler job (every 30 min) last.
Rollback: unset the flag (instant stop). Data rollback: the migration's UNDO block.

## 8. Test-store runbook

`docs/runbooks/judgeme-test-store.md`.

## 9. Real-store re-validation checklist (D1d)

Record real responses with `scripts/record_judgeme_fixture.py` (token read from the environment, never printed, shop and reviewer fields redacted at write time) into `tests/fixtures/judgeme/recorded_*.json`; the fake server replays that file via `FakeJudgeMe.from_recording`.

| ID | Check | Re-run for metric | Pass means |
|---|---|---|---|
| R1 | Field names and types of one real page vs section 1 F7 | M1, M6 | mapper needs no change, or the change is made and M1/M6 re-run on the recording |
| R2 | Ordering: fetch pages 1-3 of a 250-review store; compare `created_at`/`updated_at` sequences; edit an old review and re-fetch | M1, M3a, M4 | decide rule 3; otherwise `unknown` stays |
| R3 | Rate limit: fetch 30 pages sequentially, note any 429 and its `Retry-After` | M5 (429 cases) | decide rule 4; recorded 429 replayed in M5 |
| R4 | Does `per_page` echo, and what does the last page look like for an exact multiple of 100 | M1 | terminal rule matches; recording replayed |
| R5 | Are unpublished/hidden reviews returned in the unfiltered list; what `published`/`hidden` look like | M3a, M3b | decide rule 5 |
| R6 | Header token (`X-Api-Token`) accepted? | M5 (bad token), connect probe | `auth_transport` default confirmed or flipped |
| R7 | What a wrong token and a regenerated (old) token return (status, body) | M5 | revoked-token detection rule confirmed against reality |
| R8 | Does the free plan expose the API at all; what the response is on a plan without it | M5 | F12 resolved; a distinct typed error added if the plan error differs from a bad token |
| R9 | Edit/delete/unpublish a review in the admin and measure when the API reflects it | M3a, M4 | actual propagation delay added to the lag bounds |
| R10 | Time a moderated review: created vs published vs when it appears | M4 | W confirmed or raised |

## 10. Known limitations stated up front

- The stars value is not carried by the existing queue (same as CSV).
- Product-only edits with identical text do not update an existing extraction's product (cache is keyed by text hash).
- Throughput is bounded by the existing queue (3 rows per tick); a 2,000-review backfill takes hours, not minutes. This is a property of `ingest-tick`, not of the connector.
- Everything in section 5 is fixture-based until section 9 is run.
- Deletions are never faster than two complete full scans (section 3.7). There is no delete webhook (F11) and "absent from one scan" is indistinguishable from a page shift, so this is the price of zero false deletions.
- `oldest_first` early-stop pays about log2(pages) probe requests to find the tail, so it is not cheaper than a full scan below roughly a dozen pages (measured below: 775 requests vs 655 for the 14-day, 200-review simulation). It only pays for large stores; `unknown` is the right default until section 9 R2.
- `shop_domain` edge whitespace is stripped before validation (a paste habit); any interior whitespace or newline is rejected.

## 11. Results on contract fixtures (SYNTHETIC, not a real Judge.me store)

Provenance: code at commit `f4b8409` (branch `feat/judgeme-poller`); thresholds in section 5 were committed first (`c9f1f7d`) and are unchanged. Command: `.venv\Scripts\python.exe -m pytest tests/unit/test_judgeme_client.py tests/unit/test_judgeme_scan.py tests/unit/test_judgeme_sync.py tests/unit/test_judgeme_store.py tests/unit/test_judgeme_endpoints.py tests/unit/test_judgeme_job.py --no-cov -q` -> 177 passed. Freshness and lag figures come from re-running the same simulations with `scratchpad/measure_judgeme.py` and `measure_lags.py` (not committed; the bounds they print are asserted in `tests/unit/test_judgeme_sync.py`).

| ID | Result | Threshold | Cases (n) |
|---|---|---|---|
| M1 completeness | 100 percent in every case | 100 percent | 21 sync cases (7 sizes x 3 order modes) + 28 scan cases (7 sizes x 2 orders x echo/no echo) + growth-between-pages + page-shift |
| M2 idempotency | 0 duplicate stages; 0 rows staged in runs 2-5 (and via the job: one stage call in 5 runs); crash between stage and state re-stages 30 texts that all hit the extraction cache (30 conflicts, 0 extra rows) | 0 | 21 + 2 |
| M3a edits/deletions | all reflected: edit, unpublish, hide, delete, republish, shared-text reviews. Lags (simulated clock, T=6h, R=24h): unknown-order edit 1 run (<= T); known-order edit of an old review 23h via a full scan (bound R+T = 30h); deletion 12h unknown order (bound 2T = 12h), 48h known order (bound 2R+T = 54h) | 100 percent within the section 3.7 bounds | 7 scenarios |
| M3b false deletions | 0 across: empty-page glitch, 5xx mid-run, mid-scan deletion shift (review 150 got one strike, then cleared), page-cap incomplete scan, mass removal (needs 3 strikes) | 0 | 5 |
| M4 freshness | p95 5.75h, p50 3.33h, max 5.98h for creates, identical for `unknown`, `newest_first`, `oldest_first` (n=200 reviews over 14 days, seed 42, 59 ticks, per_page 10). HTTP requests over the run: 655 unknown, 400 newest_first, 775 oldest_first. Late publish inside the overlap window: seen within T; beyond it: seen only at the next full scan (asserted > T and <= R+T) | p95 <= 6h and max <= 6h | 3 modes + 2 late-publish cases |
| M5 failure handling | each case yields the section 3.5 behaviour with no partial writes | one test per case | client: bad token, 403, 404, 429 with/without Retry-After, 429 over cap, 429 exhausted, 5xx retried/exhausted, timeout retried/exhausted, 4 malformed pages, other 4xx; run level: 7 failure shapes leave state byte-identical; job level: bad token confirmed once then revoked, revoked after success, unknown shop, 429 deferral x2, 5xx/timeout/malformed recorded, run timeout, unexpected error, undecryptable token |
| M6 privacy | 0 occurrences of fixture email/phone/name/reviewer id in staged rows, state rows, extractions or queue arguments | 0 | 4 tests |
| M7 retention | 0 outbound requests, 0 stage/purge/state writes for a stateless org; connect refused with 409 before any probe; unreadable mode also does nothing | 0 | 3 tests |
| M8 secret hygiene | 0 occurrences of the token in logs, exception text or endpoint payloads. A real leak was found and fixed during this work: `httpx` logs the URL as an object, so the first redaction filter missed `?api_token=` on the query-transport fallback | 0 | endpoint + client tests |

Not measured and not claimable from fixtures: real response field names (R1), real ordering (R2), real rate limits (R3), whether unpublished reviews are listed (R5), real revoked-token behaviour (R7), API access on the free plan (R8), real propagation delay (R9, R10). The migration (`supabase/migrations/20261011000001_judgeme_installations.sql`) has been statically checked only (postcondition grammar, BYPASSRLS guard) and never executed against any Postgres.
