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

**Table below re-verified live 2026-08-16 (Item 164/170) — this is the actual current
state, not the "not started" state this file originally recorded.** Statement 4 is
confirmed to have been briefly live (2026-08-01, see
`ops/runbooks/bypassrls-remediation-cutover.md`'s corrected header) and is NOT live now —
treat this as re-verify-before-every-write, not as license to skip the pre-write check
above just because this table says "Yes".

| # | Statement | Done? | By whom | When | Verified via |
|---|---|---|---|---|---|
| 1 | `CREATE ROLE review_iq_migrator` + grants | **Yes** | out-of-band, exact identity/timestamp unrecorded (no tracking table exists — Item 169) | before 2026-08-16 | Re-queried live 2026-08-16: role exists, `rolbypassrls=true`, `postgres` is a member, `CREATE` on `public` confirmed |
| 2 | `CREATE ROLE review_iq_admin` + grants | **Yes** | out-of-band, unrecorded | before 2026-08-16 | Re-queried live 2026-08-16: role exists, `rolbypassrls=true`, member of `authenticated` |
| 3 | `resolve_org_for_google_location` / `resolve_org_for_shopify_shop` | **Yes** | out-of-band, unrecorded | before 2026-08-16 | Re-queried live 2026-08-16: both functions exist, owned by `review_iq_migrator`, `EXECUTE` granted to `review_iq_app` |
| 4 | `ALTER ROLE review_iq_app NOBYPASSRLS` | **No — was briefly Yes on 2026-08-01, reverted since** | applied 2026-08-01 (see runbook); reversion time/identity unrecorded | 2026-08-01 (applied), unknown (reverted) | Re-queried live 2026-08-16: `rolbypassrls=true` — the exposure is live |
| 5 | `api_keys_key_prefix_key` UNIQUE | **Yes** | out-of-band, unrecorded | before 2026-08-16 | Re-queried live 2026-08-16: constraint present on `api_keys` |
| 6 | `organization_members_user_id_key` UNIQUE | **Yes** | out-of-band, unrecorded | before 2026-08-16 | Re-queried live 2026-08-16: constraint present on `organization_members` |

## Related prerequisite status (outside the migration file itself)

**Note:** the `gcloud` commands below still say `--project=review-iq-prod` — that project
was decommissioned 2026-08-14 in favor of `reviewiq-prod-260813` (see
`deploy-cloud-run.yml`'s own 2026-08-15 correction note). Repoint `--project` before running
any of these for real.

| Item | Done? | By whom | When | Verified via |
|---|---|---|---|---|
| TOCTOU duplicate-org cleanup (runbook step 0) | **Yes** | unrecorded (found already done, 2026-08-01) | before 2026-08-01 | `SELECT user_id, count(*) FROM organization_members GROUP BY user_id HAVING count(*)>1` returns 0 rows |
| Code-level PRs deployed to both services (runbook step 1) | **Yes, as of 2026-08-16** — PR #68 (session.py/signup.py/account.py/api_key.py resolver rewrite) merged and its own pre-cutover-verification.yml gate passed | unrecorded (out-of-band) for the original 2026-08-01 partial deploy; PR #68 merge is on the record | PR #68 merged 2026-08-16 | `gcloud run services describe <service> --project=reviewiq-prod-260813 --format="value(spec.template.spec.containers[0].image)"` — confirm the image SHA is a descendant of PR #68's merge commit |
| `review-iq-admin-database-url` secret created + wired (runbook step 2) | **Yes** | out-of-band, unrecorded | before 2026-08-16 | Re-verified live 2026-08-16: `review-iq-admin` Cloud Run service's `ADMIN_DATABASE_URL` references secret `review-iq-admin-database-url`, distinct from `supabase-database-url` |

## Update discipline

Whoever completes a step edits this file's table row in the same commit/action as the step
itself (or immediately after, for a live production change with no commit of its own) — never
batched, never after the fact from memory. A stale row is worse than no row: it reads as
confirmation of something that didn't happen.
