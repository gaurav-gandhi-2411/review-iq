# Cloud Run Cost Check Runbook

Run this once a week, or any time a spend alert fires.

**Project:** `review-iq-prod`  
**Expected cost:** ₹0.00 at current traffic levels (well within Always Free)

---

## Always Free quotas (monthly reset)

| Resource | Free tier | Billing starts at |
|----------|-----------|-------------------|
| Cloud Run requests | 2,000,000 | >2M req/mo |
| Cloud Run vCPU-seconds | 180,000 | >180k vCPU-sec/mo |
| Cloud Run GiB-seconds | 360,000 | >360k GiB-sec/mo |
| Artifact Registry storage | 500 MB | >500 MB |
| Cloud Build | 120 min/day | >120 min/day |
| Secret Manager access ops | 10,000 | >10k ops/mo |
| Secret Manager active versions | 6 | >6 active versions |

---

## Step 1 — Check Cloud Billing dashboard

```bash
gcloud billing accounts list
# Open: https://console.cloud.google.com/billing/014DAE-6B3556-077365/reports?project=review-iq-prod
```

Or from CLI (credits + charges summary):
```bash
gcloud billing accounts describe 014DAE-6B3556-077365
```

---

## Step 2 — Check Cloud Run request count

```bash
# Last 30 days request count (via Metrics Explorer or CLI)
gcloud run services describe review-iq \
  --region=asia-south1 --project=review-iq-prod \
  --format="table(status.observedGeneration,status.latestReadyRevisionName)"
```

For detailed metrics, query Cloud Monitoring:
```bash
# Install monitoring component if needed: gcloud components install monitoring
gcloud monitoring metrics list --filter="metric.type:run.googleapis.com/request_count" 2>/dev/null | head -5
```

---

## Step 3 — Check Artifact Registry storage

**PowerShell (Windows — python3 not available on PATH):**
```powershell
$TOKEN = (gcloud auth print-access-token).Trim()
$resp = Invoke-RestMethod -Uri "https://artifactregistry.googleapis.com/v1/projects/review-iq-prod/locations/asia-south1/repositories/review-iq/packages/api/versions?view=FULL" `
    -Headers @{ "Authorization" = "Bearer $TOKEN" }
$total = 0
foreach ($v in $resp.versions) {
    $b = [long]($v.metadata.imageSizeBytes ?? 0)
    $total += $b
    $tags = ($v.relatedTags | ForEach-Object { $_.name.Split("/")[-1] }) -join ","
    Write-Host "  $($v.name.Split('/')[-1].Substring(0,12))  $([math]::Round($b/1MB,1)) MB  tags=[$tags]"
}
Write-Host "Total: $([math]::Round($total/1MB,1)) MB  (free limit: 500 MB)"
```

**Linux/macOS (bash + python3):**
```bash
TOKEN=$(gcloud auth print-access-token)
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://artifactregistry.googleapis.com/v1/projects/review-iq-prod/locations/asia-south1/repositories/review-iq/packages/api/versions?view=FULL" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
total = 0
for v in data.get('versions', []):
    b = int(v.get('metadata', {}).get('imageSizeBytes', 0))
    total += b
    print(f'  {v[\"name\"].split(\"/\")[-1][:12]}  {b/1024/1024:.1f} MB  tags={[t[\"name\"].split(\"/\")[-1] for t in v.get(\"relatedTags\", [])]}')
print(f'Total: {total/1024/1024:.1f} MB  (free limit: 500 MB)')
"
```

If approaching 500 MB, trigger a GC run or manually delete old versions:
```bash
gcloud artifacts docker images delete \
  "asia-south1-docker.pkg.dev/review-iq-prod/review-iq/api:OLD_TAG" \
  --project=review-iq-prod --quiet
```

---

## Step 4 — Check Secret Manager active versions

```bash
total=0
for s in groq-api-key gemini-api-key supabase-database-url admin-password-hash; do
  count=$(gcloud secrets versions list $s --project=review-iq-prod \
    --filter="state=enabled" --format="value(name)" | wc -l)
  echo "  $s: $count enabled"
  total=$((total + count))
done
echo "Total active versions: $total  (free limit: 6)"
```

---

## Step 5 — Kill switch status

The billing kill switch (Cloud Function + Pub/Sub budget alert) is documented in `ops/runbooks/killswitch-test.md`. Verify it's still enabled:

```bash
gcloud functions list --project=review-iq-prod --region=asia-south1 2>/dev/null || \
gcloud run services list --project=review-iq-prod --region=asia-south1 | grep -i kill
```

---

## When cost > ₹0.00

1. Check which service exceeded free tier (billing report will show)
2. If Cloud Run: look for runaway instance count — `gcloud run revisions list`
3. If Artifact Registry: run GC (Step 3 above)
4. If unexpected: trigger kill switch manually per `ops/runbooks/killswitch-test.md`
