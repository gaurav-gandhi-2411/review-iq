# BYPASSRLS Remediation Cutover Runbook

**Migration:** `supabase/migrations/20260801000001_role_separation_bypassrls_remediation.sql`
**Do not run this migration until every step below is done, in order.** Statement 4
(`ALTER ROLE review_iq_app NOBYPASSRLS`) removes the one thing every currently-unreviewed
code path implicitly depends on — running it before the code that no longer needs it is
deployed will break production request-serving paths, not just the ones already fixed.

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
