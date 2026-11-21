# Migration rehearsal (S22)

**Decision (GG):** no production DB credential in CI, ever -- the repo is public.
So a PR's migrations are never rehearsed against production. `.github/workflows/migration-rehearsal.yml`
rehearses them on a **restored copy of the latest encrypted backup** instead.

## Model
1. `rehearse-prod-rollback`: download the latest successful `db-backup.yml` artifact
   (`GITHUB_TOKEN`, `actions: read`); **fail if it is older than 72h**; decrypt with
   `DB_BACKUP_ENCRYPTION_KEY` (stdin, masked); create roles; restore into a throwaway `postgres:17`
   service container (zero `psql` ERROR lines required).
2. Pending = migration files in the PR checkout that are not in the restored `public._migrations`
   ledger. They are applied by `supabase/push.py` as the non-superuser `review_iq_migrator`
   (ledger + postconditions in one transaction), then `push.py --verify` audits the whole DB.
3. Row counts of every public table before/after; any table that loses rows or vanishes fails.
4. Rollback rehearsal: each pending migration's `UNDO_CASES` run on the restored data in a rolled-back
   transaction (postcondition PASS before, FAIL after the undo).
5. `undo-case` and `postconditions` jobs run the same checks against a fresh migrator-built DB.
   Job ids match claude-config's merge gate (`GATE4_EVIDENCE_CONFIG`, review-iq `migration`).

A PR touching no migration still runs; with nothing pending steps 2-4 print that and pass
trivially (the restore and `--verify` still run). Fork PRs get no secrets: the rehearsal job is
skipped, which the merge gate treats as not-green.

## What it proves
Pending migrations apply as the migrator on prod-shaped data, satisfy their postconditions, drop no
rows, and their postconditions are not vacuous.

## What it does NOT prove
- Data is **up to 24h old** (72h tolerated); rows written since are absent.
- **`public` schema only**: no `auth`/`storage` schemas, no Supabase platform roles' real
  attributes, extensions or settings (roles are recreated NOLOGIN/with the migration's attributes).
- A fresh container is not production: no load, locks, or concurrent traffic. "Rollback" means undo
  cases flip postconditions; there is no down-migration replay.

## Open conflict (needs GG's decision; not changed here)
`db-backup.yml` and `schema-drift-check.yml` already use `SUPABASE_DIRECT_URL` -- a **production
credential** -- in CI. That contradicts the "no production credential in CI" rule. Options for GG:
move backups to a non-CI runner, or accept a scoped read-only role for those two workflows only.
