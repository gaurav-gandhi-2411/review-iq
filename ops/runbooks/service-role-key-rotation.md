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
the anon key for token verification is the standard supabase-js flow. Not yet verified end to end: a real user
token through the deployed service (Plan A step 3 below is that test).

## 2. Plan A (applies; confirmed 2026-10-05): remove the consumer, then disable the legacy keys

**Correction found 2026-10-05 (S17 F2): the leaked key is a LEGACY JWT, not an `sb_secret_` key.**
Secret Manager `supabase-service-role-key` holds a legacy `service_role` JWT (starts `eyJ`, `role`
claim `service_role`, exp 2094; checked by shape, value not printed). The project also shows the
new key system. Deleting an `sb_secret_` key does nothing to a legacy JWT: the leaked key stays valid
until the **legacy API keys are disabled** in the dashboard (or the legacy JWT secret is rotated). The
local `.env` anon keys (`SUPABASE_ANON_KEY`, `VITE_SUPABASE_ANON_KEY`) are legacy JWTs too, and the
Vercel one is BELIEVED to be (value is encrypted; not decrypted), so disabling legacy keys also breaks
the anon key until the web app and the API are moved to the `sb_publishable_` key. Order matters:

1. DONE (2026-10-05): #250 merged and deployed (Cloud Run revision `review-iq-00044-svc`, image
   `sha-4d729ba...`): `_get_supabase_admin()` prefers `SUPABASE_ANON_KEY`, falls back to the
   service-role key while it is unset. Baseline: 23 `/bff/*` requests, all 200, on that revision.
2. Dashboard: copy the `sb_publishable_...` key (Project Settings -> API Keys). It is public by design.
3. Cloud Run, API verification path:
   `gcloud run services update review-iq --region=asia-south1 --project=reviewiq-prod-260813 --account=gaurav.gandhi1129@gmail.com --update-env-vars=SUPABASE_ANON_KEY=<sb_publishable key>`
   (`--update-env-vars` merges; never `--set-env-vars`). Log in at https://app.samidhareviews.xyz, load
   the dashboard, then check Cloud Run logs: `/bff/*` all 200, no 401, for 10 minutes. If 401
   appears, unset the variable (`--remove-env-vars=SUPABASE_ANON_KEY`): the service-role fallback
   resumes. Not yet verified: that GoTrue accepts a `sb_publishable_` key on `/auth/v1/user` through
   supabase-py 2.31 (the client constructor accepts the format; verified). This step is that test.
4. Vercel project `samidha-reviews-web`: set `VITE_SUPABASE_ANON_KEY` to the `sb_publishable_` key
   (Production and Preview), redeploy, log in again at the app.
5. Cloud Run: remove the service-role binding:
   `gcloud run services update review-iq ... --remove-secrets=SUPABASE_SERVICE_ROLE_KEY`; repeat the
   step 3 check.
6. Dashboard: disable the legacy API keys (the control that actually kills the leaked JWT).
   See section 4 for the exact path. Then PROVE it: a request with the old service-role key must
   return 401 (CC can run this check without printing the key).
7. Disable the Secret Manager version: `gcloud secrets versions disable 1 --secret=supabase-service-role-key --project=reviewiq-prod-260813 --account=gaurav.gandhi1129@gmail.com`; replace the three
   legacy values in the local `.env` / `web/.env.local` with the new-style ones.
8. Follow-up PR: delete the fallback and the `SUPABASE_SERVICE_ROLE_KEY` setting from the code so the
   key cannot be reintroduced silently.

Rollback: before step 6 everything is reversible (unset the env var; re-add the secret binding with
`--update-secrets=SUPABASE_SERVICE_ROLE_KEY=supabase-service-role-key:latest`). After step 6 the
legacy keys are off; re-enabling them re-validates the leaked key, so do not roll back that step,
fix forward with the publishable key instead.

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

## 4. Disabling the legacy keys in Supabase (UNVERIFIED: written from documentation, I have no dashboard access)

GG confirmed the project shows the new key system (`sb_publishable_` / `sb_secret_`). Individual
`sb_secret_` keys revoke one by one without touching the anon key or signing anyone out, but that
does not help here because the leaked key is the legacy JWT (section 2). Path, as documented:
Project Settings -> API Keys -> the "Legacy anon, service_role API keys" tab -> disable the legacy
keys. Disabling is meant to be reversible, which is why it is the step to do last and check. Does it
sign users out? BELIEVED no (user sessions are signed with the JWT signing key, a separate setting);
UNVERIFIED. If the dashboard instead offers only "rotate JWT secret", that signs everyone out: tell
CC before pressing it.

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
