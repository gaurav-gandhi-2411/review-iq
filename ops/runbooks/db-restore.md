# Runbook: DB Restore from Backup Artifact

**Project:** review-iq  
**Supabase ref:** enqpluazgxewepchdeut  
**Backup source:** GitHub Actions artifact (`db-backup` workflow)  
**Artifact retention:** 90 days (hard limit on GitHub free tier — older dumps are permanently deleted)

---

## Limitations (read before proceeding)

| Limitation | Detail |
|---|---|
| **Retention** | Artifacts are deleted automatically after 90 days. There is no offline copy unless you download and store one manually. |
| **Geo-redundancy** | Artifacts are stored in GitHub's US data centres. If GitHub has an outage, access to artifacts is unavailable until it recovers. |
| **Granularity** | One dump per day (nightly cron at 18:00 UTC). Data written after the last dump and before an incident is lost. |
| **Restore is manual** | There is no automated restore path. This runbook must be followed by a human. |
| **Paid upgrade path** | Supabase Pro ($25/mo) provides automated daily backups + point-in-time recovery (PITR). This has been deferred; the current free-tier workflow is the accepted trade-off. |

---

## Prerequisites

- `psql` and `gunzip` available locally, **or** a fresh Supabase project / local Postgres 15 instance as the restore target.
- `gh` CLI authenticated (`gh auth login`) for downloading the artifact.
- Connection string for the **target** database (NOT prod — see Step 3 note).
- `SUPABASE_DIRECT_URL` of the target — port 5432, not the pooler port 6543.

---

## Step 1 — Find the right artifact

List recent backup artifacts via the `gh` CLI:

```bash
gh run list \
  --workflow=db-backup.yml \
  --repo gaurav-gandhi-2411/review-iq \
  --status success \
  --limit 10
```

Note the **run ID** of the backup you want (column 1 of the output). Prefer the most recent successful run unless you are recovering from data corruption, in which case pick the last clean run before the corruption.

Alternatively, browse in the GitHub UI:

```
https://github.com/gaurav-gandhi-2411/review-iq/actions/workflows/db-backup.yml
```

Click the relevant run → **Artifacts** section → download the `.sql.gz` file.

---

## Step 2 — Download the artifact

Using the `gh` CLI (replace `<RUN_ID>` with the run ID from Step 1):

```bash
# Download to the current directory
gh run download <RUN_ID> \
  --repo gaurav-gandhi-2411/review-iq \
  --dir ./restore-tmp

ls ./restore-tmp/
# Expected: review-iq-db-YYYYMMDD-HHMMSS.sql.gz/review-iq-db-YYYYMMDD-HHMMSS.sql.gz
# (gh wraps the file in a subdirectory named after the artifact)
```

Verify the download is intact before proceeding:

```bash
DUMPFILE=$(find ./restore-tmp -name "*.sql.gz" | head -1)
gunzip -t "${DUMPFILE}" && echo "Archive OK" || echo "CORRUPT — re-download"
```

---

## Step 3 — Choose a restore target

> **IMPORTANT: Never restore over the production database.**
>
> The dump was created with `--clean --if-exists`, meaning it issues `DROP TABLE` statements before `CREATE TABLE`. Running it against prod will delete all production data.

Appropriate restore targets:

| Target | When to use |
|---|---|
| **Fresh Supabase project** (staging) | Full DR test or schema migration rehearsal |
| **Local Postgres 15** (`docker run postgres:15`) | Quick data inspection or development |
| **Supabase shadow DB** | If you use Supabase CLI migrations locally |

---

## Step 4 — Restore

Set the target connection string (replace the placeholder with your target's actual URL):

```bash
TARGET_URL="postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres"
# or for local Docker:
# TARGET_URL="postgresql://postgres:postgres@localhost:5432/postgres"

DUMPFILE=$(find ./restore-tmp -name "*.sql.gz" | head -1)
```

Run the restore:

```bash
gunzip -c "${DUMPFILE}" | psql \
  --no-password \
  "${TARGET_URL}"
```

Expected output: a stream of `DROP TABLE`, `CREATE TABLE`, `COPY`, `ALTER TABLE` lines, ending without `ERROR:` lines. Some `ERROR:  role "..." does not exist` lines from `ALTER OWNER` statements on Supabase-managed objects are harmless — Supabase's internal roles do not exist in a fresh project.

If you see `psql: error: connection to server ... failed`, check:
- The target URL is port **5432** (direct), not 6543 (pooler). `psql` does not work with the pooler.
- The Supabase project is not paused (see `ops/runbooks/supabase-pause-recovery.md`).
- SSL is required: add `?sslmode=require` to the URL if psql complains about SSL.

---

## Step 5 — Verify the restore

Connect to the target and check table presence and approximate row counts:

```bash
psql "${TARGET_URL}" <<'SQL'
-- Table presence
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
ORDER BY table_name;

-- Row counts for key tables
SELECT 'organizations'     AS tbl, COUNT(*) AS rows FROM public.organizations
UNION ALL
SELECT 'authenticity_audits',       COUNT(*)         FROM public.authenticity_audits
UNION ALL
SELECT 'org_members',               COUNT(*)         FROM public.org_members
UNION ALL
SELECT 'api_keys',                  COUNT(*)         FROM public.api_keys;
SQL
```

Expected: all tables present, row counts match your mental model of prod data volume (or match a recent count from the production Supabase dashboard).

If `organizations` shows 0 rows but you expect data, the dump may have been taken during a Supabase pause window (the project auto-pauses on the free plan after 7 days of inactivity — see `ops/runbooks/supabase-pause-recovery.md`). In that case, restore the next older artifact.

---

## Step 6 — Clean up

```bash
rm -rf ./restore-tmp
```

Do not leave the dump file on disk longer than needed — it contains all production data in plaintext (gzip is compression, not encryption).

---

## Upgrade path

When the project moves to Supabase Pro:

1. Enable **Point-in-Time Recovery (PITR)** in the Supabase dashboard → Project Settings → Database → Backups.
2. Disable or delete the `db-backup` workflow — it is superseded by Supabase's built-in daily snapshots and PITR.
3. Update this runbook to reference the Supabase restore UI instead.

PITR on Supabase Pro supports restores to any second within the retention window (7 days on Pro, 30 days on Enterprise), with no manual download required.
