# Cold Start Tuning Runbook

**Current config:** `--min-instances=0` (scales to zero when idle)  
**Observed cold start (after scale-to-zero):** not yet measured from true zero; container was pre-warmed by Cloud Run during deployment verify  
**Health-check warm latency:** ~87ms

---

## What causes cold start

1. Cloud Run allocates a new VM and pulls the container image (~98 MB compressed — fast)
2. Python interpreter starts and imports the full app: FastAPI, Pydantic, asyncpg, psycopg2, argon2-cffi, Groq/Gemini clients, structlog, Prometheus
3. `lifespan` runs: `setup_logging()` only (SQLite migrate is skipped in cloud-run mode)
4. Container signals ready → Cloud Run routes traffic

For this image, expect **3–8 seconds** from true zero to first response. The argon2-cffi and psycopg2 native extensions are the heaviest import cost.

Note: Cloud Run adds `startup-cpu-boost: true` automatically on Python images, giving extra CPU during startup to reduce this.

---

## Current cold start measurements

| Date | Revision | Measurement | Note |
|------|----------|-------------|------|
| 2026-05-11 | `review-iq-00002-gxv` | 272ms | Container was pre-warmed by deploy health check — NOT a true cold start |

Update this table as true cold starts are observed (e.g. after overnight idle).

---

## Knobs available

### Option 1 — Do nothing (current: free)

`min-instances=0` means no cost while idle. Cold starts are infrequent at low traffic levels (B2B API, not consumer-facing). One 5-second cold start per session is acceptable.

### Option 2 — One warm instance (costs ~$10-12/mo)

```bash
gcloud run services update review-iq \
  --region=asia-south1 --project=review-iq-prod \
  --min-instances=1
```

One instance running continuously at 1 vCPU / 1Gi = ~360k vCPU-sec/mo = exactly the free tier limit.  
**Risk:** One concurrent request spike could push over. Only use if cold start is actually causing customer complaints.

### Option 3 — Keep-warm cron ping (free)

Use Cloud Scheduler (3 jobs free/mo) to ping `/health` every 10 minutes:
```bash
gcloud scheduler jobs create http review-iq-keepwarm \
  --schedule="*/10 * * * *" \
  --uri="https://review-iq-ajjrytb3na-el.a.run.app/health" \
  --http-method=GET \
  --location=asia-south1 \
  --project=review-iq-prod
```
This prevents scale-to-zero during active hours. Costs 1 Cloud Scheduler job (3 free), negligible request count.

### Option 4 — Reduce image size

`supabase==2.30.0` is installed but unused (no `from supabase import` anywhere in app code). Removing it from `pyproject.toml` would reduce image size and import time.

```bash
# After removing supabase from pyproject.toml:
uv remove supabase
uv sync
# Rebuild image and redeploy
```

Estimated savings: ~15-20 MB compressed, ~100ms startup time.

---

## Diagnosing actual cold start latency

After a period of zero traffic (instance should scale to zero):
```bash
# Time the first request after idle
time curl -sf https://review-iq-ajjrytb3na-el.a.run.app/health
```

Cloud Run logs will show container startup events. Check:
```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="review-iq" AND textPayload:"Starting server"' \
  --project=review-iq-prod --limit=5 --format="table(timestamp,textPayload)"
```
