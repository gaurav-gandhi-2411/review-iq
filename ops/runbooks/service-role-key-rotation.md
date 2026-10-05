# Supabase service-role key: consumers, rotation order, rollback

Written 2026-10-05 (Session 17, W1) after the key and the postgres password were printed into a
session transcript (S16). The postgres password has been reset. **The service-role key has not been
rotated.** It bypasses RLS entirely, so treat it as compromised until it is revoked.

## 1. Every consumer (verified 2026-10-05 against the running system)

| # | Consumer | Evidence (command, 2026-10-05) | Effect of revoking the key |
|---|---|---|---|
| 1 | Cloud Run `review-iq` (public), env `SUPABASE_SERVICE_ROLE_KEY` <- Secret Manager `supabase-service-role-key:latest` (one enabled version, created 2026-08-13) | `gcloud run services describe review-iq --region=asia-south1 --project=reviewiq-prod-260813 --format=json`, env names + `valueFrom.secretKeyRef` only | Every dashboard/BFF request fails with 401 (see "what the key does" below) until the new key is deployed |
| 2 | Cloud Run `review-iq-admin` | same command: env is `DEPLOY_TARGET, SERVICE_ROLE, SUPABASE_DATABASE_URL, ADMIN_DATABASE_URL, ADMIN_PASSWORD_HASH`; **no** service-role key | none |
| 3 | GitHub Actions | `gh secret list` (repo: `CLOUDFLARE_*`, `DB_BACKUP_ENCRYPTION_KEY`, `GEMINI_API_KEY`, `SUPABASE_DIRECT_URL`), `gh api .../environments/{Preview,Production}/secrets` (Production: `SUPABASE_DATABASE_URL` only), grep of `.github/workflows/*.yml` for `secrets.*` | none: no workflow reads it |
| 4 | Vercel `samidha-reviews-web` | project env list (names; values not decrypted): `VITE_SUPABASE_ANON_KEY`, `VITE_API_URL`, `VITE_SUPABASE_URL` | none: browser bundle holds only the anon key |
| 5 | Local `.env` at the repo root (name present and non-empty; `web/.env.local`, `.env.local` and `benchmark/vernacular_v2/.env.benchmark.local` do not set it) | `python scripts/env_keys.py` | local scripts/tests that load Settings; replace the line after rotation |
| 6 | Cloud Scheduler jobs | they authenticate with `INGEST_TICK_TOKEN` / `DIGEST_TRIGGER_TOKEN` / `DETECTOR_SWEEP_TRIGGER_TOKEN`, not this key | none |
| 7 | Copies in transcripts/logs from the S16 session | **unknown, cannot be enumerated from here** | none, but they are why the key must be revoked, not merely replaced |

What the key does in code: exactly one use. `app/auth/signup.py::_get_supabase_admin()` builds a
Supabase client with it and `verify_supabase_jwt()` calls `client.auth.get_user(jwt)`. That function
is the dashboard login check for `app/api/account.py`, `app/auth/session.py`, `google_auth.py` and
`shopify_auth.py`. `/v1` API-key traffic does not go through it.

Finding that changes the plan: `get_user(jwt)` does not need a privileged key. Verified 2026-10-05
against the live project: `GET /auth/v1/user` with the **anon** key as `apikey` passes the API-key gate
(HTTP 403 "invalid JWT" for a garbage bearer), while a bogus `apikey` gets 401 "Invalid API key". Using
the anon key for token verification is the standard supabase-js flow. Not yet verified: a real user
token through the deployed service (do step B3 below).

## 2. Plan A (recommended): remove the consumer, then revoke. No swap, no downtime.

The key never needs a replacement, so there is no window in which production lacks access.

1. Merge and deploy the PR that makes `_get_supabase_admin()` prefer `SUPABASE_ANON_KEY` and fall
   back to the service-role key when it is unset (behaviour unchanged at deploy time).
2. Set the env var on the public service (the anon key is public by design, so a plain env var is
   fine, no secret needed):
   `gcloud run services update review-iq --region=asia-south1 --project=reviewiq-prod-260813 --account=gaurav.gandhi1129@gmail.com --update-env-vars=SUPABASE_ANON_KEY=<anon key from the dashboard>`
   (`--update-env-vars` merges; never `--set-env-vars`, which drops every other variable.)
3. Verify on the running service: log in at https://app.samidhareviews.xyz with a real account, load
   the dashboard (a BFF call), and check Cloud Run logs for 401s on `/bff/*` for 10 minutes.
4. Remove the dependency: `gcloud run services update review-iq ... --remove-secrets=SUPABASE_SERVICE_ROLE_KEY`
   (new revision; repeat step 3).
5. Revoke the key in Supabase (section 4). Nothing in production reads it any more.
6. Disable the Secret Manager version: `gcloud secrets versions disable 1 --secret=supabase-service-role-key --project=reviewiq-prod-260813 --account=gaurav.gandhi1129@gmail.com`
   (never leave an enabled version of a revoked secret; see secret-rotation.md for the quota rule).
7. Delete the local `.env` line.

Rollback: before step 4, nothing was removed, so rollback is `gcloud run services update-traffic review-iq --to-revisions=<previous>=100`.
After step 4, rollback is `--update-secrets=SUPABASE_SERVICE_ROLE_KEY=supabase-service-role-key:latest`
(only valid while the key is not yet revoked; after step 5 there is nothing to roll back to, which is
why step 3 must pass first).

## 3. Plan B: keep the consumer, swap the key (only if Plan A is not taken)

Order, so production never loses access: **issue new -> update every consumer -> verify -> revoke old.**

1. Issue the new key (section 4). Do not revoke the old one yet.
2. `printf '%s' '<new key>' | gcloud secrets versions add supabase-service-role-key --data-file=- --project=reviewiq-prod-260813 --account=gaurav.gandhi1129@gmail.com` (value piped, never echoed).
3. Redeploy so `:latest` is re-read: `gcloud run services update review-iq --region=asia-south1 --project=reviewiq-prod-260813 --account=gaurav.gandhi1129@gmail.com --update-labels=rotated=$(date +%s)`.
4. Verify as in Plan A step 3.
5. Revoke the old key. Disable the old Secret Manager version (`versions disable <old>`).
6. Replace the local `.env` line.

Rollback: until step 5 the old key still works, so re-point `supabase-service-role-key:latest` by disabling
the new version and redeploying. After step 5 only a freshly issued key can restore service.

## 4. Issuing and revoking in Supabase (UNVERIFIED, written from documentation, not from the dashboard)

Supabase has two key systems and which one this project shows decides the procedure. Check in
Project Settings -> API Keys:

* **New keys** (`sb_publishable_...` / `sb_secret_...`): create a new secret key, later delete the old
  one; each revoked independently. Plan B step 1 and 5 are exactly that.
* **Legacy keys** (JWT `anon` / `service_role`): the service-role JWT cannot be revoked on its own. It
  is invalidated by rotating the project **JWT secret** (Project Settings -> JWT Keys / JWT Settings),
  which also invalidates the legacy anon key and signs out every user session. Do this in a quiet
  window, then update `VITE_SUPABASE_ANON_KEY` in Vercel (redeploy the web app) and `SUPABASE_ANON_KEY`
  on Cloud Run in the same pass. Under Plan A this is the whole procedure.

What CC needs from GG to finish: which of the two key systems the project shows, and, for Plan A, the
anon/publishable key value pasted to the Cloud Run command only (never into chat), or GG runs step 2.

## 5. The postgres password reset (checked 2026-10-05)

* `review_iq_migrator` over the IPv4 session pooler (`aws-1-ap-southeast-2.pooler.supabase.com:5432`,
  user `review_iq_migrator.<ref>`): connects, `rolbypassrls = true`. `supabase/push.py --dry-run`
  against it: 43 already applied, 0 would apply.
* The old postgres password from the local `.env` is rejected by the pooler (`password
  authentication failed`).
* The direct host `db.<ref>.supabase.co` does not resolve from this machine (IPv6 only). `push.py`
  reads `SUPABASE_DIRECT_URL`, so point it at the pooler form for the migrator.
* Stale credentials that still carry the old postgres password: GitHub secret `SUPABASE_DIRECT_URL`
  (role `postgres`; `db-backup`, `schema-drift-check` and `migration-drift-check` fail with `password
  authentication failed for user "postgres"`; last good nightly backup 2026-09-20, failing every night
  since 2026-09-21, issues #242/#243/#244), Secret Manager `supabase-direct-url`, and the local `.env`.
  Fix: set the GitHub secret to the migrator pooler URL (least privilege) or to the new postgres
  password; update `supabase-direct-url` the same way.
* Production runtime does not use the postgres role: Cloud Run uses `review_iq_app` (pooler 6543) and
  `review_iq_admin`; `/health` returns 200.

## 6. Keeping values out of transcripts

`scripts/hook_guard_secrets.py` (wired in `.claude/settings.json`) denies reading `.env`-type files and
dumping the environment, and blocks any tool output that contains a service-role JWT, a connection
string with a password or a known API-key shape. `python scripts/env_keys.py <file>` lists variable
names without values.
