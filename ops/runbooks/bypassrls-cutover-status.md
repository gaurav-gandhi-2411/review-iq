# BYPASSRLS Cutover Status

**This file is a courtesy signal, not a lock.** Two sessions can both read "not started" in
the same instant and both proceed — a markdown file provides no mutual exclusion. The
*binding* mitigation is the mandatory live check below, run immediately before every write,
every time, regardless of what this file says. Update this file *after* a step completes, as
a record for the next person/session to read — never treat it as authoritative on its own
before a write.

## Mandatory pre-write check

**Before running ANY of the writes in the table below (migration statements, the `ALTER
ROLE`, or a Cloud Run deploy), re-query the live objects that step creates or modifies and
confirm the state matches what's expected for that step — abort if it doesn't.**

```sql
-- Roles
SELECT rolname, rolbypassrls FROM pg_roles
WHERE rolname IN ('review_iq_app','review_iq_admin','review_iq_migrator');

-- SECURITY DEFINER functions
SELECT proname FROM pg_proc
WHERE proname IN ('resolve_org_for_google_location','resolve_org_for_shopify_shop');

-- New UNIQUE constraints
SELECT conname FROM pg_constraint
WHERE conname IN ('api_keys_key_prefix_key','organization_members_user_id_key');
```

```bash
# Deployed image tag, both services
gcloud run services describe review-iq --project=review-iq-prod --region=asia-south1 \
  --format="value(spec.template.spec.containers[0].image)"
gcloud run services describe review-iq-admin --project=review-iq-prod --region=asia-south1 \
  --format="value(spec.template.spec.containers[0].image)"
```

If any object already exists that a not-yet-attempted step should be about to create, or any
object is missing that a step you're about to run *depends on* already existing — **stop**.
That is exactly the partial-application state a concurrent session could produce, and it
must be diagnosed before proceeding, never assumed away.

## Migration statement status

Migration: `supabase/migrations/20260801000001_role_separation_bypassrls_remediation.sql`

| # | Statement | Done? | By whom | When | Verified via |
|---|---|---|---|---|---|
| 1 | `CREATE ROLE review_iq_migrator` + grants | No | — | — | `SELECT rolname FROM pg_roles WHERE rolname='review_iq_migrator'` |
| 2 | `CREATE ROLE review_iq_admin` + grants | No | — | — | `SELECT rolname FROM pg_roles WHERE rolname='review_iq_admin'` |
| 3 | `resolve_org_for_google_location` / `resolve_org_for_shopify_shop` | No | — | — | `SELECT proname FROM pg_proc WHERE proname IN (...)` |
| 4 | `ALTER ROLE review_iq_app NOBYPASSRLS` | No | — | — | `SELECT rolbypassrls FROM pg_roles WHERE rolname='review_iq_app'` (expect `false`) |
| 5 | `api_keys_key_prefix_key` UNIQUE | No | — | — | `SELECT conname FROM pg_constraint WHERE conname='api_keys_key_prefix_key'` |
| 6 | `organization_members_user_id_key` UNIQUE | No | — | — | `SELECT conname FROM pg_constraint WHERE conname='organization_members_user_id_key'` |

## Related prerequisite status (outside the migration file itself)

| Item | Done? | By whom | When | Verified via |
|---|---|---|---|---|
| TOCTOU duplicate-org cleanup (runbook step 0) | **Yes** | unrecorded (found already done, 2026-08-01) | before 2026-08-01 | `SELECT user_id, count(*) FROM organization_members GROUP BY user_id HAVING count(*)>1` returns 0 rows |
| Code-level PRs deployed to both services (runbook step 1) | Partial — deployed manually (tag `bypassrls-cutover`), not through the CI pipeline | unrecorded | 2026-08-01T04:42:24Z | `gcloud run services describe <service> --format="value(spec.template.spec.containers[0].image)"` |
| `review-iq-admin-database-url` secret created + wired (runbook step 2) | No | — | — | `gcloud run services describe review-iq-admin --format="value(spec.template.spec.containers[0].env)"` — expect `ADMIN_DATABASE_URL` referencing `review-iq-admin-database-url`, not `supabase-database-url` |

## Update discipline

Whoever completes a step edits this file's table row in the same commit/action as the step
itself (or immediately after, for a live production change with no commit of its own) — never
batched, never after the fact from memory. A stale row is worse than no row: it reads as
confirmation of something that didn't happen.
