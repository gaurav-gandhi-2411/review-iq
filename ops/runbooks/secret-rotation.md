# Secret Rotation Runbook

**Project:** review-iq-prod  
**Service account:** `review-iq-runner@review-iq-prod.iam.gserviceaccount.com`

---

## Key rules (read before rotating anything)

Secret Manager free tier: **6 active (enabled) versions** across all secrets, all time.  
Steady state: **12 secrets × 1 active version = 12 active versions** (9 as of 2026-07-11,
+3 from the 2026-08-01 BYPASSRLS remediation cutover: `review-iq-admin-database-url`,
`review-iq-migrator-database-url`, `supabase-direct-url`) — exceeds the free tier by 6. At
$0.06/version/month, that's ~$0.36/month — trivial, but not $0, and worth knowing rather than
assuming free. This runbook previously only tracked 4 of the 9 secrets and its quota loop
undercounted accordingly; both are fixed below.

**2026-07-11 cleanup:** `resend-from-email` had 2 enabled versions (version 1 from 2026-07-02
was never disabled after version 2 was added on 2026-07-07) — a live violation of the rule
below, found while fixing this doc. Disabled version 1; Cloud Run already resolved `latest` to
version 2, so this had zero live effect. If you find another secret with >1 enabled version,
the same fix applies: `gcloud secrets versions disable OLD_VERSION --secret=NAME --project=review-iq-prod`.

**Rotation transiently raises the count to 10.** The moment you add a new version the old one
is still enabled. You must disable the old version immediately to return to 9. Never leave two
enabled versions on the same secret.

**Rule: create new version → disable old version → done. No exceptions.**  
If you add a new version and walk away without disabling the old one, you pay for an extra
version indefinitely until you fix it. Two concurrent rotations = 11 active versions.

**Admin password is different from all other secrets.** You cannot store a raw password in Secret Manager — the app verifies an argon2id hash, not a plaintext password. When rotating `admin-password-hash` you must generate a new hash locally first (see procedure below), then store the hash. The plaintext password goes into your password manager, never into Secret Manager.

---

## Secrets managed

Verified live against the actual Secret Manager inventory (`gcloud secrets list
--project=review-iq-prod`) on 2026-07-11 — matches `ops/runbooks/cloud-run-deploy.md`'s
9-secret env-var table. Re-verify the same way before trusting this if it's been a while.
**Updated 2026-08-01** for the BYPASSRLS remediation cutover (`ops/runbooks/bypassrls-
remediation-cutover.md`) — 3 secrets added, `supabase-database-url`'s description
corrected (see that row).

| Secret name (kebab-case) | Cloud Run env var | What it is |
|--------------------------|-------------------|------------|
| `groq-api-key` | `GROQ_API_KEY` | Groq LLM API key (primary inference) |
| `gemini-api-key` | `GEMINI_API_KEY` | Google Gemini API key (dev fallback) |
| `supabase-database-url` | `SUPABASE_DATABASE_URL` | Supabase pooler URL, port 6543 (transaction mode). Connects as `review_iq_app` (non-superuser, member of `authenticated`). **As of the 2026-08-01 BYPASSRLS remediation cutover, this role no longer holds BYPASSRLS** — the 2026-07-26 description below was accurate at the time but is now stale; see `supabase/migrations/20260801000001_role_separation_bypassrls_remediation.sql` and `ops/runbooks/bypassrls-remediation-cutover.md` for the current state and why. Rotating this secret's password also requires `ALTER ROLE review_iq_app WITH PASSWORD '...'` on the database first. |
| `admin-password-hash` | `ADMIN_PASSWORD_HASH` | argon2id hash of admin HTTP Basic password |
| `review-iq-admin-database-url` | `ADMIN_DATABASE_URL` (review-iq-admin service only) | Added 2026-08-01. Connects as `review_iq_admin` (BYPASSRLS, member of `authenticated`) — a genuinely separate role from `review_iq_app`, used only by the private `review-iq-admin` Cloud Run service (IAM-gated, not the public service). Before this cutover, `ADMIN_DATABASE_URL` pointed at this same `supabase-database-url` secret by accident — see the cutover runbook. |
| `review-iq-migrator-database-url` | *(none — never referenced by any deployed service)* | Added 2026-08-01. Connects as `review_iq_migrator` (BYPASSRLS, schema-scoped to `public`), for running future migrations only. Deliberately not granted to any Cloud Run service account — reachability from a request-serving path must stay impossible, not just unlikely. |
| `resend-api-key` | `RESEND_API_KEY` | Resend transactional email API key |
| `resend-from-email` | `RESEND_FROM_EMAIL` | Verified sender address for alert/digest emails |
| `supabase-url` | `SUPABASE_URL` | Supabase project REST URL (used for JWT verification) |
| `supabase-service-role-key` | `SUPABASE_SERVICE_ROLE_KEY` | Bypasses RLS — highest-sensitivity secret in this table. Rotate first if any compromise is suspected. |
| `unsubscribe-signing-key` | `UNSUBSCRIBE_SIGNING_KEY` | HMAC key signing one-click unsubscribe tokens |
| `supabase-direct-url` | *(none — never referenced by any deployed service)* | Added 2026-08-01, for running migrations that need `CREATEROLE` (`review_iq_app`/`review_iq_migrator` don't have it). Connects as `postgres`. Highest-sensitivity secret in this table alongside `supabase-service-role-key` — never grant any Cloud Run service account access to it. |

Not tracked here (plain env vars, not Secret Manager — see `cloud-run-deploy.md` for why):
`DIGEST_TRIGGER_TOKEN`, `INGEST_TICK_TOKEN`. Shopify/Google OAuth secrets
(`SHOPIFY_CLIENT_SECRET`, `SHOPIFY_TOKEN_ENCRYPTION_KEY`, `GOOGLE_CLIENT_SECRET`,
`GOOGLE_TOKEN_ENCRYPTION_KEY`, `GOOGLE_PUBSUB_PUSH_TOKEN`) are not yet live on this service
(GBP connector pending API approval per project history) — add them here when they go live.

---

## Rotating SHOPIFY_TOKEN_ENCRYPTION_KEY / GOOGLE_TOKEN_ENCRYPTION_KEY

These encrypt Shopify access tokens / Google refresh tokens at rest
(`shopify_installations.access_token_enc`, `google_business_installations.refresh_token_enc`).
As of 2026-07-11 (audit finding #6) both `_build_fernet()` functions
(`app/api/webhooks/shopify.py`, `app/api/webhooks/google.py`) support a **comma-separated key
list** — `encrypt_token` always uses the first key; `decrypt_token` tries each key in order.
This makes rotation possible without breaking already-installed merchants, which a single-key
value cannot do (rotating it would make every existing installation's stored token permanently
undecryptable).

**To rotate:**
1. Generate a new key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
2. Set the secret value to `"<new_key>,<old_key>"` (new key FIRST — it's what new encryptions use).
3. Deploy. Existing installations keep decrypting via the old key (now second in the list); any
   merchant who re-authorizes from this point gets re-encrypted under the new key.
4. **Do not remove the old key from the list** until you're confident nothing still needs it —
   there is no batch re-encryption job, so a token is only re-encrypted if its merchant
   re-authorizes. Dropping the old key early permanently breaks decryption for any installation
   still encrypted under it (silently — the webhook just drops with `Token decryption failed`,
   caught and logged, not a crash).
5. If you must force re-encryption faster than organic re-auth, that requires a dedicated
   migration script (decrypt-with-old-key, re-encrypt-with-new-key, per installation) — not built
   as of this writing; scope it separately if you actually need to fully retire an old key on a
   deadline rather than indefinitely.

---

## Rotation procedure

### Step 1 — Add the new version

```bash
# Pipe the new value directly; avoid writing it to disk or shell history
printf '%s' 'NEW_VALUE_HERE' | gcloud secrets versions add SECRET_NAME \
  --data-file=- \
  --project=review-iq-prod
```

Note the new version number in the output (e.g. `Created version [2]`).

### Step 2 — Disable the old version immediately

```bash
# Disable version N-1 right after adding version N — never skip this step
gcloud secrets versions disable OLD_VERSION_NUMBER \
  --secret=SECRET_NAME \
  --project=review-iq-prod
```

Active versions should remain at 1 per secret after this step. Verify:

```bash
gcloud secrets versions list SECRET_NAME \
  --project=review-iq-prod \
  --format="table(name,state)"
```

Expected output:
```
NAME  STATE
2     enabled
1     disabled
```

### Step 3 — Redeploy Cloud Run to pick up the new version

Cloud Run is configured with `--update-secrets` using the `latest` alias, so the new version is picked up on the next deployment. If you need immediate rollout without a code change:

```bash
gcloud run services update review-iq \
  --region=asia-south1 \
  --project=review-iq-prod \
  --no-traffic   # deploy new revision without shifting traffic
# then shift traffic after confirming health:
gcloud run services update-traffic review-iq \
  --region=asia-south1 \
  --project=review-iq-prod \
  --to-latest
```

### Step 4 — Verify

```bash
# Confirm the service is healthy after rotation
curl -sf https://SERVICE_URL/health | jq .
```

---

## Admin password rotation (extra steps)

Rotating `admin-password-hash` requires generating a new argon2id hash first:

```bash
# Generate a new hash locally (uv run from repo root)
uv run python -c "
import argon2, secrets
ph = argon2.PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
password = secrets.token_urlsafe(32)
print('Password:', password)
print('Hash:    ', ph.hash(password))
"
```

Store the **password** in your password manager before proceeding. Then use the **hash** as the new secret value in Step 1.

---

## Emergency: destroy a compromised secret

If a secret value is compromised, destroy the exposed version immediately:

```bash
# Destroy (irreversible) — use only for confirmed compromise
gcloud secrets versions destroy VERSION_NUMBER \
  --secret=SECRET_NAME \
  --project=review-iq-prod
```

Then rotate (Steps 1–4 above) with a freshly generated value.

---

## Quota accounting after rotation

The free tier is 6 versions; steady state here is 12 (see "Key rules" above — a small
paid overage, ~$0.36/month). After any rotation, verify each secret is back to exactly 1
enabled version:

```bash
# Count enabled versions across all 12 secrets
for s in groq-api-key gemini-api-key supabase-database-url admin-password-hash \
         resend-api-key resend-from-email supabase-url supabase-service-role-key \
         unsubscribe-signing-key review-iq-admin-database-url \
         review-iq-migrator-database-url supabase-direct-url; do
  n=$(gcloud secrets versions list $s --project=review-iq-prod \
    --filter="state=enabled" --format="value(name)" | wc -l)
  echo "$s: $n"
  if [ "$n" -ne 1 ]; then echo "  ^ WARNING: expected exactly 1 enabled version"; fi
done
```

Every line should read `secret-name: 1` during normal operation — any secret showing a
different count means an old version wasn't disabled after a rotation (see the 2026-07-11
`resend-from-email` cleanup above for the fix).
