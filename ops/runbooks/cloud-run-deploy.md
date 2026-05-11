# Cloud Run Deploy Runbook

**Service:** `review-iq`  
**Project:** `review-iq-prod`  
**Region:** `asia-south1` (Mumbai)  
**Image registry:** `asia-south1-docker.pkg.dev/review-iq-prod/review-iq/api`

---

## Standard redeploy (new image version)

```bash
# 1. Build and push via Cloud Build (free: 120 min/day)
cd /path/to/review-iq
gcloud builds submit \
  --tag asia-south1-docker.pkg.dev/review-iq-prod/review-iq/api:TAG \
  --region=asia-south1 \
  --project=review-iq-prod

# 2. Deploy new revision WITHOUT sending traffic (--no-traffic on existing service)
gcloud run deploy review-iq \
  --image=asia-south1-docker.pkg.dev/review-iq-prod/review-iq/api:TAG \
  --region=asia-south1 \
  --project=review-iq-prod \
  --service-account=review-iq-runner@review-iq-prod.iam.gserviceaccount.com \
  --set-secrets="GROQ_API_KEY=groq-api-key:latest,GEMINI_API_KEY=gemini-api-key:latest,SUPABASE_DATABASE_URL=supabase-database-url:latest,ADMIN_PASSWORD_HASH=admin-password-hash:latest" \
  --set-env-vars="DEPLOY_TARGET=cloud-run,ENVIRONMENT=production" \
  --port=8080 --min-instances=0 --max-instances=3 \
  --memory=1Gi --cpu=1 --timeout=120 --concurrency=80 \
  --allow-unauthenticated \
  --no-traffic   # ← new revisions go here; no user traffic yet

# 3. Get new revision name
gcloud run revisions list --service=review-iq --region=asia-south1 --project=review-iq-prod \
  --format="table(metadata.name,status.observedGeneration,spec.containers[0].image)"

# 4. Smoke-test the new revision at its direct URL
REVISION_URL=$(gcloud run revisions describe REVISION_NAME \
  --region=asia-south1 --project=review-iq-prod --format="value(status.url)")
curl -sf "$REVISION_URL/health"

# 5. Promote to 100% traffic once health check passes
gcloud run services update-traffic review-iq \
  --region=asia-south1 --project=review-iq-prod \
  --to-revisions=REVISION_NAME=100
```

---

## Canary / split traffic

```bash
# Send 10% to new revision, 90% to existing
gcloud run services update-traffic review-iq \
  --region=asia-south1 --project=review-iq-prod \
  --to-revisions=NEW_REVISION=10,OLD_REVISION=90

# Promote to 100% after monitoring
gcloud run services update-traffic review-iq \
  --region=asia-south1 --project=review-iq-prod \
  --to-revisions=NEW_REVISION=100
```

---

## Rollback

```bash
# Roll back to a specific previous revision immediately
gcloud run services update-traffic review-iq \
  --region=asia-south1 --project=review-iq-prod \
  --to-revisions=PREVIOUS_REVISION_NAME=100
```

To find previous revision names:
```bash
gcloud run revisions list --service=review-iq --region=asia-south1 --project=review-iq-prod \
  --format="table(metadata.name,metadata.creationTimestamp,status.conditions[0].status)"
```

---

## Service configuration (current as of v0.2.0)

| Flag | Value | Why |
|------|-------|-----|
| `--memory` | 1Gi | argon2 64MB/verify + asyncpg + FastAPI baseline |
| `--cpu` | 1 | 1 vCPU; thread pool = 5 workers |
| `--timeout` | 120s | batch extraction ceiling |
| `--concurrency` | 80 | asyncio; auth queues at thread pool (5 concurrent argon2) |
| `--min-instances` | 0 | scale to zero; cold start accepted at free tier |
| `--max-instances` | 3 | prevents runaway billing |

---

## First-deploy note

`--no-traffic` is not supported when creating a brand-new service (no prior revision exists).  
For the very first deploy, omit `--no-traffic` — the first revision automatically gets 100% traffic.  
All subsequent deploys should use `--no-traffic` + manual promote pattern above.
