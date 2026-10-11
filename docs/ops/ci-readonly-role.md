# CI read-only database role (`ci_readonly`)

Rule (GG, S23): **no write-capable production credential in CI.** CI workflows that touch
production use `SUPABASE_RO_URL`, the `ci_readonly` role. Migrations stay local (`supabase/push.py`).

| Workflow | Needs | Change |
|---|---|---|
| `db-backup.yml` | `pg_dump --schema=public` (schema + all rows) | `SUPABASE_RO_URL` |
| `schema-drift-check.yml` | `pg_catalog` / `information_schema` reads | `SUPABASE_RO_URL` |
| `migration-drift-check.yml` | `SELECT` on `public._migrations`, catalog reads | `SUPABASE_RO_URL` mapped into the env var name `push.py` reads |

Not changed: throwaway-container values in `bypassrls-container-check.yml`, `pre-cutover-verification.yml`
and `supabase/ci/apply_migrations_ci.py`; `security-bypassrls-check.yml` uses `SUPABASE_DATABASE_URL`
(the `review_iq_app` DSN), a different secret.

## RLS and BYPASSRLS: what a read-only role can and cannot do

Every tenant table has RLS enabled and policies only `TO anon` / `TO authenticated`. A non-owner role
that is neither (and has no `BYPASSRLS`) matches no policy. So **a read-only role cannot take a complete
backup without `BYPASSRLS`**: default `pg_dump` (`row_security=off`) fails on those tables, and
`--enable-row-security` silently dumps zero rows. Table ownership would also work but is write-capable.

Therefore `ci_readonly` has `BYPASSRLS` and **SELECT-only** grants (no INSERT/UPDATE/DELETE/TRUNCATE, no
CREATE, no role attributes). It is read-only in effect, but sensitive: it reads every tenant's rows,
the same data the backup artifact holds (gpg-encrypted) and strictly less than the credential it replaces.

**S0 policy exception (named):** the S0 rule is "`review_iq_app` never holds `BYPASSRLS`; `review_iq_migrator`
must" (`docs/data-ownership.md`, `security-bypassrls-check.yml`, `check_migrations_no_bypassrls_grant.py`).
`ci_readonly` is a third, different role and does not violate it, but it is an explicit exception to
the spirit "BYPASSRLS only where unavoidable": allowed for `ci_readonly` only, SELECT-only, backups/drift only.
The role is created from `supabase/ops/`, not `supabase/migrations/`, so the migration guard does not scan it.

## Schema-drift side effect (fixed in this PR)

`information_schema.role_table_grants` / `routine_privileges` list only grants visible to the connecting
role, so as `ci_readonly` they return almost nothing. `scripts/extract_schema_snapshot.py` and
`push.py::_grant_mismatches` now read `pg_class.relacl` / `pg_proc.proacl` via `aclexplode` (readable by
everyone). The rehearsal asserts the snapshot taken as `ci_readonly` equals the one taken as superuser.

## GG-session steps (order matters)

1. Merge this PR (human merge; touches workflows/secrets). Confirm `CI Read-only Role Rehearsal` was green.
2. Generate a >= 24 char password into Secret Manager (secret name of your choice, e.g. `ci-readonly-password`).
   Do not paste it in chat or argv.
3. Locally, as the Supabase `postgres` admin role (member of `review_iq_migrator`), with the password in the
   environment only:
   `psql "<admin DSN, from Secret Manager>" -f supabase/ops/ci_readonly_role.sql`
   The script is idempotent, fails closed on a short/unset password, and ends with in-script assertions
   (no CREATE on `public`, SELECT on every table, no write privilege).
4. Check what the role can EXECUTE (functions default to PUBLIC execute; SECURITY DEFINER ones run as owner):
   ```sql
   SELECT p.oid::regprocedure FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname = 'public' AND p.prosecdef AND has_function_privilege('ci_readonly', p.oid, 'EXECUTE');
   ```
   Expected: empty, or only read-only resolvers you have reviewed. Any writer listed: `REVOKE EXECUTE ... FROM PUBLIC`
   in a normal migration first. Also expect `SELECT has_schema_privilege('ci_readonly','public','CREATE')` = false.
5. Build the URL: session-mode pooler, port 5432. The pooler username is probably `ci_readonly.<project-ref>`
   (unverified: confirm in the Supabase dashboard). Percent-encode special characters in the password.
   Set it: `gh secret set SUPABASE_RO_URL --repo gaurav-gandhi-2411/review-iq` (value from stdin, never argv).
6. Dispatch and verify green: `gh workflow run db-backup.yml`, `schema-drift-check.yml`, `migration-drift-check.yml`.
   For the backup, also run `db-restore-test.yml` on the new artifact (proves rows, not just DDL).
7. Only after all three are green: delete the old `SUPABASE_DIRECT_URL` GitHub secret
   (`gh secret delete SUPABASE_DIRECT_URL`). Reading current secrets is by name only (`gh secret list`).
8. Rotation: rerun step 3 with a new password, then update `SUPABASE_RO_URL`.

## Rollback

Re-set `SUPABASE_DIRECT_URL` and revert the workflow edits. The role can be dropped with
`DROP OWNED BY ci_readonly; DROP ROLE ci_readonly;` (after secrets no longer reference it).

## Known limits

- `default_transaction_read_only = on` is a soft guard; the real wall is the absence of write grants.
- Server logs may record `ALTER ROLE ... PASSWORD` (Postgres statement logging); rotate if logs are exposed.
- `push.py --dry-run` postconditions of a *pending* migration that calls `has_*_privilege` are catalog-based and fine;
  any future postcondition reading `information_schema.*_privileges` would mislead for this role.
