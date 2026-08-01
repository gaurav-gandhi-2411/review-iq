# BYPASSRLS Remediation Cutover Runbook

**Migration:** `supabase/migrations/20260801000001_role_separation_bypassrls_remediation.sql`
**Do not run this migration until every step below is done, in order.** Statement 4
(`ALTER ROLE review_iq_app NOBYPASSRLS`) removes the one thing every currently-unreviewed
code path implicitly depends on — running it before the code that no longer needs it is
deployed will break production request-serving paths, not just the ones already fixed.

## CUTOVER COMPLETE — 2026-08-01

Applied against production. `review_iq_app.rolbypassrls = False`, verified live. The
one-shot "apply the whole file, then fix the admin secret" order below (steps 2-3) was
revised at execution time into three passes to eliminate an admin-surface downtime
window the literal step order would have caused — recorded here as what actually
happened, since it differs from the plan above:

- **Pass 1** — ran migration statements 1–3 only (create `review_iq_migrator` +
  `review_iq_admin` roles, create the two `SECURITY DEFINER` webhook-lookup functions).
  Zero behavior change: `review_iq_app.rolbypassrls` confirmed still `True` immediately
  after. Both new roles' passwords generated and set out-of-band; each stored in its own
  Secret Manager secret (`review-iq-admin-database-url`, `review-iq-migrator-database-url`
  — the latter deliberately NOT granted to any Cloud Run service account, per the
  migration's own "never referenced by the deployed app" design). Re-ran the full smoke
  suite (both services) — unchanged from pre-Pass-1.
- **Pass 2** — provisioned `review-iq-admin-database-url`, granted
  `roles/secretmanager.secretAccessor` to `review-iq-runner@review-iq-prod.iam.gserviceaccount.com`
  only, redeployed `review-iq-admin` staged (`--no-traffic` → smoke-test, including a
  direct DB-level cross-org read as `review_iq_admin` proving the admin surface works on
  its own credential, independent of the HTTP Basic auth layer this session couldn't
  authenticate through → promote). This step happening *before* Pass 3 (not after, as the
  original step numbering implied) is what eliminates the admin-surface downtime window:
  by the time `review_iq_app` loses BYPASSRLS, the admin service is already running on its
  own separate bypass-holding role and is unaffected.
- **Pass 3** — ran migration statements 4–6 (`ALTER ROLE review_iq_app NOBYPASSRLS`, both
  UNIQUE constraints). Verified immediately: `review_iq_app.rolbypassrls = False`, both
  constraints present. Full `tests/integration/` suite run for real against production
  (not simulated): **18/18 passed**, including both new `TestReviewIqAppCredentialIsolation`
  tests — own-org read succeeds AND cross-org read is blocked, using `review_iq_app`'s raw
  credential with no `SET ROLE` at all. This resolves the verifier's ADDITION 1 concern:
  RLS policies scoped `TO authenticated` do correctly cover `review_iq_app` via its
  inherited role membership, with no additional policy work required. Final smoke suite
  (both services, including a real extraction and the BFF auth-rejection path) passed
  clean post-cutover.

**Pre-flight (step 0) executed as-is**, after re-verifying the duplicate-org data was
unchanged and getting explicit confirmation: the two abandoned race-condition orgs
(`e784fc49-...`, `d96ddca6-...`) deleted, `d58203be-...` (the active one) kept.

**Credential note:** step 3's original `$SUPABASE_DIRECT_URL` reference assumed a shell
env var; in practice this was `gcloud secrets versions access latest --secret=supabase-direct-url
--project=review-iq-prod` (the secret didn't exist in Secret Manager until this cutover —
it was added specifically to unblock this, since `review_iq_app` itself lacks
`CREATEROLE` and cannot run statement 1).

### Rollback (not needed — cutover succeeded clean on the first attempt; recorded for the
### next person who hits a problem, since an untested rollback path is the same bug class
### this repo has hit before)

**If `review_iq_app` needs BYPASSRLS back immediately** (legitimate access broken,
Pass 3's both-directions test would have caught this before it shipped, but if it
surfaces later anyway):

```sql
ALTER ROLE review_iq_app BYPASSRLS;
```

One statement, takes effect immediately for new transactions (existing connections in
`_set_tenant()`'s `SET LOCAL ROLE authenticated` path are unaffected either way, since
that path never depended on `review_iq_app`'s own BYPASSRLS to begin with — confirmed
during Pass 3's verification). This is the single lever: it restores the pre-cutover
state for every code path in one statement, no code deploy required, no service restart
required, revertible within the time it takes to run one `ALTER ROLE`.

**If Pass 2's `review-iq-admin` redeploy needs rolling back** (admin surface broken on
its own credential):

```bash
gcloud run services update-traffic review-iq-admin --region=asia-south1 --project=review-iq-prod \
  --to-revisions=review-iq-admin-00002-hp9=100
```

Reverts to the last revision still using the shared `supabase-database-url` secret —
functionally identical to pre-Pass-2 (admin surface shares review_iq_app's credential
again), safe as long as Pass 3 hasn't run yet (once it has, review_iq_app no longer holds
BYPASSRLS, so reverting the admin service to the shared secret would leave *it* also
without BYPASSRLS — combine with the `ALTER ROLE ... BYPASSRLS` rollback above if both
need reverting together).

**The two new UNIQUE constraints (statements 5–6) were not rolled back and don't need to
be** — the accompanying code (PR #62/#63, merged and deployed before this cutover) already
handles both constraints gracefully (collision retry, race-handling), so they're safe to
leave in place independent of any BYPASSRLS rollback decision.

## 0. Pre-flight data cleanup (required — the migration will fail without this)

Live query (read-only, run 2026-08-01) found **one existing `organization_members.user_id`
with 3 rows**, which statement 6's `UNIQUE (user_id)` constraint would reject outright:

```
user_id 521b043b-f442-435a-ada6-6c8d69f804eb (gg5678g@gmail.com):
  org gg5678g-ff3d3e (e784fc49-...)  created 2026-06-20T17:33:21.107012Z  0 usage records
  org gg5678g-e9d02a (d96ddca6-...)  created 2026-06-20T17:33:21.107012Z  3 usage records
  org gg5678g-eaf5f5 (d58203be-...)  created 2026-06-20T17:33:22.292027Z  103 usage records, quota=10000
```

The first two orgs share the **exact same microsecond timestamp** — two concurrent
`/auth/provision` calls raced before either committed, exactly the TOCTOU gap statement 6's
migration comment describes. The third org is clearly the one in real use (103 usage
records, an elevated 10000 quota suggesting a manually-set dev/test value). This was not
executed by this session — confirm the classification above yourself, then run:

```sql
-- Delete the two abandoned race-condition orgs, keeping d58203be (the active one).
-- CASCADE removes their api_keys/organization_members/extractions/usage_records rows too.
DELETE FROM public.organizations
WHERE id IN ('e784fc49-d407-4fc1-9998-f763c6c448d5', 'd96ddca6-d83f-4972-946b-0fb6b2d3842d');
```

Re-run the duplicate check before proceeding:

```sql
SELECT user_id, count(*) FROM public.organization_members GROUP BY user_id HAVING count(*) > 1;
-- must return zero rows
```

(`api_keys.key_prefix` had zero duplicates as of 2026-08-01 — statement 5 needs no cleanup.)

## 1. Deploy the code changes first

Merge and deploy the code-level PRs in this remediation series (webhook SECURITY DEFINER
refactor, keygen retry-on-collision, signup race handling, config.py comment fix) to
**both** Cloud Run services (`review-iq` and `review-iq-admin`) before touching the
database. Most of it works correctly whether or not `review_iq_app` still holds
BYPASSRLS — safe to deploy ahead of the DB cutover.

**Exception, bounded but real:** the two webhook lookups
(`app/api/webhooks/{google,shopify}.py`) now call
`public.resolve_org_for_{google_location,shopify_shop}()`, which doesn't exist until
migration statement 3 runs. Until then, both handlers catch `UndefinedFunction` and drop
the webhook with a 200 + log line (same fail-safe-never-a-default-org design this file
already uses for an unrecognized `location_name`/`shop_domain`) — **inbound Shopify/Google
webhooks are silently dropped, not erroring loudly**, for whatever window elapses between
this deploy and step 3. Keep that window short; don't deploy this and then sit on step 3
for days.

## 2. Provision `review_iq_admin`'s own secret (new infrastructure — not yet done)

`ADMIN_DATABASE_URL` on `review-iq-admin` currently points at the **same** Secret Manager
secret as `SUPABASE_DATABASE_URL` (`supabase-database-url`), confirmed via
`gcloud run services describe review-iq-admin --project review-iq-prod --region asia-south1`.
There is no genuinely separate `review_iq_admin` credential deployed anywhere yet. After
running statements 1–2 of the migration (which create the `review_iq_migrator` and
`review_iq_admin` roles) and setting `review_iq_admin`'s password out-of-band:

```bash
# 1. Generate the new secret's connection string (same host/port/dbname as the existing
#    supabase-database-url, only the user+password differ), then:
gcloud secrets create review-iq-admin-database-url --project=review-iq-prod --replication-policy=automatic
echo -n "<the new review_iq_admin connection string>" | gcloud secrets versions add review-iq-admin-database-url --project=review-iq-prod --data-file=-

# 2. Point the admin service at the NEW secret, not the shared one:
gcloud run services update review-iq-admin --project=review-iq-prod --region=asia-south1 \
  --update-secrets=ADMIN_DATABASE_URL=review-iq-admin-database-url:latest
```

This adds a 10th Secret Manager secret — `ops/runbooks/secret-rotation.md`'s free-tier
count (already over the 6-version free tier at 9) goes to 10, ~$0.02/month. Add it to that
runbook's tracked-secrets table once created.

## 3. Apply the migration

```bash
# review_iq_migrator's own bootstrap needs to run AS an existing bypass-capable role
# (postgres, per this repo's existing migration convention) -- same as every other
# migration in supabase/migrations/.
psql "$SUPABASE_DIRECT_URL" -f supabase/migrations/20260801000001_role_separation_bypassrls_remediation.sql
```

Set both new roles' passwords out-of-band immediately after (same pattern as
`review_iq_app`'s own 20260726 migration):

```sql
ALTER ROLE review_iq_migrator WITH PASSWORD '<generated>';
ALTER ROLE review_iq_admin WITH PASSWORD '<generated>';
```

## 4. Verify

```bash
uv run pytest tests/integration/ -v -m integration
```

`test_role_bypassrls.py` and the new `TestReviewIqAppCannotBypassRLS` class in
`test_rls_isolation.py` are expected to be **RED right now** (pre-cutover) — they assert
the target state, not the current one. They should turn green immediately after step 3.
If they don't, stop and do not consider this cutover complete.

## 5. Confirm production traffic still works

Hit `/health`, run a real extraction end to end, exercise `/bff/keys` (create/list/revoke),
and confirm a Shopify/Google webhook still resolves org_id correctly (via the new
SECURITY DEFINER functions) — before considering this done.
