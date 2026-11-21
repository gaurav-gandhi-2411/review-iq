# Plan: move Supabase from ap-southeast-2 (Sydney) to ap-south-1 (Mumbai)

Status: PLAN ONLY (S23). Nothing here has been executed. GG decides (options at the end).
Measurement artifact: `reports/ops/db_latency_s23.json` (script `scripts/measure_db_latency.py`,
base commit 9245b85). Labels: VERIFIED = observed this session; BELIEVED = inferred/estimated.

## 1. Findings

| Claim | Status | Evidence |
|---|---|---|
| Cloud Run `review-iq` runs in asia-south1 (Mumbai), project reviewiq-prod-260813 | VERIFIED | `gcloud run services describe` |
| `SUPABASE_DATABASE_URL` (secret `supabase-database-url`) host is `aws-1-ap-southeast-2.pooler.supabase.com`, port 6543 (transaction pooler) | VERIFIED (host+port only read; nothing else printed) | Secret Manager, parsed in memory |
| So the DB is in ap-southeast-2 (Sydney) | VERIFIED from the pooler host name; corroborated by the RTT below | |
| Repo runbooks (`ops/runbooks/supabase-limits.md`, `supabase-pause-recovery.md`, `connection-modes.md`) say Mumbai / `aws-0-ap-south-1` | VERIFIED STALE | contradicted by the live secret; fix in a follow-up |
| One network round trip Mumbai to the DB pooler is ~219 ms | VERIFIED | TCP connect p50 218.8 ms (n=60) from a Cloud Run Job in asia-south1 |
| A new psycopg2 connection + `SELECT 1` costs ~1.77 s | VERIFIED | job: total p50 1773.8, p95 1791.4, p99 1826.7 ms (n=60); split connect 1333 + SELECT 440 ms; matches the logged `/health` 200s |
| A new connection is ~8 round trips (1774 / 219) | VERIFIED arithmetic | TCP + TLS + startup/auth + pooler backend checkout; cost is RTT-bound |
| Reused open connection: `SELECT 1` = 221 ms (1 RTT) | VERIFIED | job reused p50 221.1, p99 228.4 (n=200) |
| `ThreadedConnectionPool` getconn + SELECT + rollback = 656 ms | VERIFIED, but this is 3 RTT (psycopg2 sends BEGIN, SELECT, and my explicit ROLLBACK). With autocommit it should be ~1 RTT | BELIEVED for the autocommit figure |
| The Mumbai path is stable: p99/p50 = 1.03 for new connections | VERIFIED | job; the local machine (NOT the production path) saw connect p99 25.1 s |

Local-machine reference (NOT production path): new-conn total p50 2662 ms, p95 6500, p99 25775; reused p50 320 ms; TCP p50 316 ms.
The 13-22 s prod stalls were NOT reproduced in 60 Cloud Run trials (max 1.83 s); their cause (pooler-side
queueing, free-tier compute, packet loss) is UNVERIFIED. PR #494's 8 s guard and diagnostics should stay.

## 2. DB share of real request latency (last 48 h of Cloud Run logs, status 200)

Source: `httpRequest.latency`, service review-iq, 48 h, 18,819 rows. New-connection cost C = 1.774 s (measured p50).
Connections per request were counted by reading code.

| Route | n | p50 s | p95 s | New DB conns/request (code) | DB share = k*C / p50 (BELIEVED) |
|---|---|---|---|---|---|
| GET /health | 17,347 | 1.77 | 1.79 | 1 (`app/api/ops.py::_ping_postgres`) | 1 x 1.774 / 1.77 = ~100% |
| POST /internal/ingest/tick (idle) | 1,440 | 1.99 | 2.02 | 1 (`ingest_worker.py` claim; empty queue returns after the claim) | 1.774 / 1.99 = ~89% |
| POST /demo/extract | 3 | 6.07 | 6.26 | 2 (`check_and_increment_demo_request_pg`, `record_demo_extraction_cost_pg`, each `_db_connect()`) | 3.548 / 6.07 = ~58% (n=3, weak) |
| POST /v2/extract (API key) | 0 in window | n/a | n/a | at least 4: key lookup (`api_key.py`), cache get, usage and extraction writes (`v2/extract.py`, each storage call its own connect) | at least 7.1 s of connect cost per request; not observed |
| BFF / dashboard endpoints | 0 in window | n/a | n/a | at least 1 each (`bff/router.py` bare `psycopg2.connect`) | not measurable: no traffic logged |

The window has essentially no authenticated traffic (monitors, probes and 3 demo calls), so real-user route latency is
UNMEASURED. 18 `/health` 503s at 3.01 s were the old 3 s guard.

## 3. Latency saving if co-located (BELIEVED, not measured)

Model: new-connection cost ~ 8 RTT + small server CPU. Cloud Run asia-south1 to an AWS ap-south-1 pooler is cross-provider
but same metro; assume RTT 2-10 ms (UNMEASURED; re-run the script against the new project before cutover). At 5 ms:
8 x 5 = 40 ms + ~30-50 ms TLS/SCRAM CPU = ~0.1 s per new connection (range 0.06-0.15 s) vs 1.774 s now, a saving of ~1.65-1.7 s.

| Route | now (p50) | estimated after |
|---|---|---|
| /health | 1.77 s | ~0.1 s |
| ingest tick (idle) | 1.99 s | ~0.3 s |
| /demo/extract | 6.07 s | ~2.7 s (2 x ~1.67 saved) |
| /v2/extract (at least 4 conns) | not observed | at least ~6.6 s saved per call |

Alternative needing no migration (BELIEVED, from measured building blocks): reuse connections. A reused-connection
`SELECT 1` is 221 ms vs 1774 ms, saving ~1.55 s per DB call. A process-level pool against the transaction pooler is compatible
with `SET LOCAL` (transaction-scoped, per `ops/runbooks/connection-modes.md`), but every statement still pays 219 ms per round trip,
so multi-query requests stay slow.

## 4. Migration plan (DO NOT EXECUTE without GG's go)

### 4.1 Supabase constraints (sources fetched 2026-10-12)
- Mumbai `ap-south-1` is an offered project region: https://supabase.com/docs/guides/platform/regions
- Free plan: two free projects across all orgs where you are Owner/Admin; paused projects do not count:
  https://supabase.com/docs/guides/platform/billing-on-supabase . A second free project is possible only if GG has fewer than 2
  active free projects. UNVERIFIED how many exist; check the dashboard first.
- Free projects pause after inactivity (repo runbook says 7 days; the docs page fetched did not state a number). Keep the new
  project active; do not rely on a paused old project as the only copy.
- 500 MB database cap on free (billing page). Current DB size UNVERIFIED (query `pg_database_size` read-only in preflight).
- Dashboard project-to-project restore does not carry Auth settings, API keys, Storage files, Edge Functions, extension settings:
  https://supabase.com/docs/guides/platform/migrating-within-supabase/dashboard-restore . The new project has new anon/service-role
  keys and a new JWT secret (that page does not mention JWT; inferred from "API keys not migrated", BELIEVED).
- No in-place region change for an existing project (BELIEVED; confirm in the dashboard), so a new project is required.
- Free tier has no PITR or automated backups (`ops/runbooks/db-restore.md`); our backup is the nightly GitHub artifact.

### 4.2 What the current backup contains (VERIFIED from code)
- `.github/workflows/db-backup.yml`: nightly 18:00 UTC; `pg_dump --clean --if-exists --schema=public` (postgres:17 image) via the
  session pooler (5432); gzip; gpg symmetric AES256 using `DB_BACKUP_ENCRYPTION_KEY`; artifact `*.sql.gz.gpg`, 90-day retention.
- Restore: `ops/runbooks/db-restore.md`; automated restore test `db-restore-test.yml` + `scripts/restore_one.sh` +
  `scripts/verify_restore.py` (tables, row counts vs COPY blocks, RLS, policies). Not run in this task.
- GAP: the dump is `public` only. It does NOT contain `auth.users` / `auth.identities` (Supabase Auth, used by `app/auth/signup.py`
  and the web app through `VITE_SUPABASE_*`) or role definitions/passwords (`review_iq_app`, `review_iq_migrator`). The migration needs
  an extra auth-data dump (data-only, `auth.users` and `auth.identities`) or users must re-register / reset passwords. GG decision.

### 4.3 Steps
1. Preflight (read-only, no downtime): record DB size and row counts; count Supabase projects and free slots; confirm the region in the
   dashboard; take a fresh manual dump (public + auth data) and restore-verify it into a throwaway local postgres:17 with `verify_restore.py`.
2. Create the new project in `ap-south-1` (GG, dashboard; owner gaurav.gandhi1129@ per the sole-identity mandate). Record ref, URL,
   anon key, service-role key and JWT secret in a password manager only.
3. Schema: `uv run python supabase/push.py` against the new project's session-pooler/direct URL (reads `.env`), then
   `push.py --verify`. This applies all 46 migrations with their postconditions and creates `review_iq_app` (LOGIN, member of
   `authenticated`). Role passwords are in no dump: set with `ALTER ROLE ... PASSWORD` per `ops/runbooks/secret-rotation.md`.
4. BYPASSRLS gate: the migrations create `review_iq_app` WITH BYPASSRLS; prod's S0 cutover revoked it
   (`supabase/cutover/20260801000001_statement4_revoke_bypassrls.sql`, a separate explicit act). After loading data, apply that statement on
   the new DB and run the bypassrls checks (`ops/runbooks/bypassrls-cutover-status.md`, `security-bypassrls-check` workflow). Skipping it leaves
   the new DB weaker than prod.
5. Rehearsal: load the data (data-only for `public` COPY blocks and auth data into the migrated schema, or restore the full `--clean` dump
   into the empty new DB), run `verify_restore.py verify`, and run `scripts/measure_db_latency.py` from a Cloud Run Job against the NEW
   pooler host (same recipe as S23). Go/no-go: new-connection total p50 < 0.5 s.
6. Secrets: new versions of `supabase-database-url` (new transaction pooler, port 6543) and `supabase-url` in Secret Manager
   (`--account=gaurav.gandhi1129@gmail.com`, project reviewiq-prod-260813); anon/service-role keys wherever stored; GitHub secret
   `SUPABASE_DIRECT_URL` (db-backup, drift checks); Vercel `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY`; re-enter Supabase Auth settings by
   hand on the new project (Site URL, redirect URLs, Google provider, SMTP/Resend). The service reads secrets as `latest`, so a new version only
   takes effect on a new revision (that is itself a production deploy).
7. Cutover with write freeze: (a) disable Cloud Scheduler `ingest-tick` and `digest-daily` (`scripts/toggle_scheduler_jobs.sh`) and stop
   writes (no app-level freeze exists today, BELIEVED; accept a maintenance window or add one); (b) final dump of the old DB; (c) restore into
   the new DB; (d) `verify_restore.py`; (e) new Cloud Run revision on the new secrets via CI (`deploy-cloud-run.yml`, GG-gated, rule 31a);
   (f) redeploy Vercel with new `VITE_SUPABASE_*`; (g) smoke: `/health`, one demo extraction, one login, one authenticated `/v2` call, one ingest
   tick; (h) re-enable Scheduler jobs; (i) point the `db-backup.yml` secret at the new DB.
8. Soak 7-14 days with the old project untouched as rollback. No deletion without the rule-55b cross-check (grep repos and bundles for the old
   ref `enqpluazgxewepchdeut` and the old pooler host). Update the stale Mumbai claims in the runbooks.

### 4.4 Downtime estimate (BELIEVED)
Assumptions: DB <= 500 MB (free cap); effective transfer >= 5 MB/s so <= ~2 min each way; restore run from the Mumbai side (a Cloud Run
Job or a nearby machine), not over a 219 ms RTT per DDL statement from Sydney-far networks. Freeze window: dump 2-5 min + restore 5-10 min +
verify 2 min + secret/revision rollout 5 min + smoke 5 min = ~20-30 min (Vercel rebuild in parallel). The JWT secret change invalidates
sessions: all logged-in users must log in again.

### 4.5 Rollback
Before step 7(e): abort and re-enable the Scheduler; users saw nothing. After (e): redeploy the previous revision (or re-point the secrets) to
the old project, revert Vercel envs. Writes made on the new DB since cutover are lost unless exported and replayed (accepted for a window
under ~1 h). The plan never modifies the old project.

### 4.6 Risk register
| Risk | Impact | Mitigation |
|---|---|---|
| auth schema not in backups | users cannot log in | include auth data in the migration dump or accept re-registration; test a login before cutover |
| New DB has BYPASSRLS on `review_iq_app` (migrations create it) | cross-tenant exposure | step 4 gate: apply cutover statement and run the bypassrls checks |
| Role passwords not carried | app cannot connect | `ALTER ROLE` and secret update; test the DSN before cutover |
| Free project limit (2) / pause rules | cannot create or the idle copy pauses | check slots first; Pro ($25/mo) is the escape hatch (spend: GG) |
| DB size near 500 MB | restore fails | preflight size check |
| Hidden consumers of the old ref | outage | rule 55b sweep; known: Cloud Run secrets, Vercel `VITE_SUPABASE_*`, GH `SUPABASE_DIRECT_URL`, Auth redirect URLs |
| Cross-cloud RTT higher than assumed | smaller saving | measure the new host from Mumbai before cutover |
| The 13-22 s stalls are a pooler/free-compute issue that follows us | tail not fixed | unexplained and not reproduced in 60 trials; keep PR #494 diagnostics; re-measure on the new project |

## 5. Options for GG (GG decides)
- A. Do nothing; keep the 8 s guard. Cost: ~1.77 s per DB call, 6 s or more on multi-call routes. No risk.
- B. Connection reuse only (small PR, no migration): in-process pool against the 6543 pooler, autocommit for pings. Saves ~1.5 s per call
  (building blocks VERIFIED: 221 ms vs 1774 ms) but keeps the 219 ms per-round-trip floor. Low risk, reversible. Cheapest first step.
- C. Region migration to Mumbai per section 4 (~20-30 min freeze; needs a free slot or Pro, an auth-data decision, the BYPASSRLS gate).
  Removes the RTT floor: ~1.65 s per new connection and 219 ms to a few ms per query (BELIEVED).
- D. B then C (my pick, BELIEVED best): B is quick and independent; do C only after preflight confirms a free slot, DB size and the auth-data approach.
