# Secret Rotation Runbook

**Project:** review-iq-prod  
**Service account:** `review-iq-runner@review-iq-prod.iam.gserviceaccount.com`

---

## Key rules (read before rotating anything)

Secret Manager free tier: **6 active (enabled) versions** across all secrets, all time.  
Steady state: **4 secrets × 1 active version = 4 active versions**.

**Rotation transiently raises the count to 5.** The moment you add a new version the old one is still enabled — you are at 5. You must disable the old version immediately to return to 4. Never leave two enabled versions on the same secret.

**Rule: create new version → disable old version → done. No exceptions.**  
If you add a new version and walk away without disabling the old one, you consume a free-tier slot permanently until you fix it. Two concurrent rotations = 6 active versions = quota ceiling hit.

**Admin password is different from all other secrets.** You cannot store a raw password in Secret Manager — the app verifies an argon2id hash, not a plaintext password. When rotating `admin-password-hash` you must generate a new hash locally first (see procedure below), then store the hash. The plaintext password goes into your password manager, never into Secret Manager.

---

## Secrets managed

| Secret name (kebab-case) | Cloud Run env var | What it is |
|--------------------------|-------------------|------------|
| `groq-api-key` | `GROQ_API_KEY` | Groq LLM API key (primary inference) |
| `gemini-api-key` | `GEMINI_API_KEY` | Google Gemini API key (dev fallback) |
| `supabase-database-url` | `SUPABASE_DATABASE_URL` | Supabase pooler URL, port 6543 (transaction mode) |
| `admin-password-hash` | `ADMIN_PASSWORD_HASH` | argon2id hash of admin HTTP Basic password |

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

After any rotation the active-version count must stay ≤ 6:

```bash
# Count enabled versions across all secrets
for s in groq-api-key gemini-api-key supabase-database-url admin-password-hash; do
  gcloud secrets versions list $s --project=review-iq-prod \
    --filter="state=enabled" --format="value(name)" | wc -l
done | paste -sd+ | bc
```

Output should be `4` during normal operation.
