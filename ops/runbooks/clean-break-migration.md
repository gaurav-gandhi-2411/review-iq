# Clean-break migration: review-iq-prod → reviewiq-prod-260813

Target billing account: `01285B-91E4CB-70AD7E` (gandhi1129). Old project (`review-iq-prod`,
still on `01285B` from the earlier in-place migration) stays live as fallback until the new
project is verified, then gets deleted. Do not start until the `01285B` project-link quota is
confirmed resolved.

## 0. Prerequisites (once quota clears)

```
gcloud projects create reviewiq-prod-260813 --name="Review IQ"
gcloud billing projects link reviewiq-prod-260813 --billing-account=01285B-91E4CB-70AD7E
```
Grant yourself Owner via Console (SOLO_MUST_INVITE_OWNERS — API-only grants cap at Editor).
Enable required APIs:
```
gcloud services enable run.googleapis.com secretmanager.googleapis.com \
  cloudscheduler.googleapis.com cloudbilling.googleapis.com \
  cloudbuild.googleapis.com firebase.googleapis.com \
  --project=reviewiq-prod-260813
```
Add Firebase to the project:
```
firebase projects:addfirebase reviewiq-prod-260813
```

## 1. Copy secrets (12 total, none GCP-resource-bound)

```
for s in admin-password-hash gemini-api-key groq-api-key resend-api-key resend-from-email \
         review-iq-admin-database-url review-iq-migrator-database-url supabase-database-url \
         supabase-direct-url supabase-service-role-key supabase-url unsubscribe-signing-key; do
  gcloud secrets create "$s" --project=reviewiq-prod-260813 --replication-policy=automatic
  gcloud secrets versions access latest --secret="$s" --project=review-iq-prod \
    | gcloud secrets versions add "$s" --project=reviewiq-prod-260813 --data-file=-
done
```
Verify each new secret's latest version matches the old one before proceeding — spot check at
least `supabase-service-role-key` and `admin-password-hash` (highest blast radius if wrong).

## 2. Deploy the Cloud Run service

Deploy `review-iq` (region `asia-south1`, same as original) from source via the repo's existing
CI/CD deploy path — do not hand-roll a `gcloud run deploy` that skips whatever build steps CI
normally runs. Confirm memory/CPU/concurrency/scaling flags match the original service (`gcloud
run services describe review-iq --project=review-iq-prod --format=export` — diff against what
gets deployed) — DealHunter's migration OOM'd once from a dropped `--memory` flag; don't repeat
that here.

## 3. Firebase Hosting rewrite (this is the actual "api.samidhareviews.xyz" mechanism)

`api.samidhareviews.xyz` is NOT a Cloud Run domain mapping — confirmed via live DNS + REST check
(2026-08-13): it's a CNAME to `review-iq-prod.web.app`, routed through a Firebase Hosting rewrite
(`ops/firebase-hosting/firebase.json`) to the Cloud Run service, same project.

```
cd ops/firebase-hosting
firebase deploy --only hosting --project reviewiq-prod-260813
```
`firebase.json`'s `rewrites[0].run.serviceId`/`region` don't need to change — they already point
at `review-iq`/`asia-south1`, which is exactly what gets redeployed in step 2 under the new
project.

Then in Firebase Console (new project) → Hosting → Add custom domain → `api.samidhareviews.xyz`.
This issues NEW verification TXT + A/CNAME records — **you'll need to update these at the
registrar**. Google-managed cert issuance follows automatically once DNS verifies (can take
anywhere from minutes to ~24h). Do not repoint the live DNS until the new project's Hosting site
answers correctly on its own `.web.app` URL first.

## 4. Cloud Scheduler jobs (3, HTTP target)

```
gcloud scheduler jobs describe review-iq-ingest-tick --project=review-iq-prod --location=asia-south1 \
  --format="value(httpTarget.uri,httpTarget.httpMethod,httpTarget.oidcToken.serviceAccountEmail)"
```
Recreate each with the same schedule against the new project's Cloud Run URL (or the still-being-
propagated `api.samidhareviews.xyz`, once DNS is live) and a fresh OIDC-invoking service account
on the new project:
```
gcloud scheduler jobs create http review-iq-ingest-tick \
  --project=reviewiq-prod-260813 --location=asia-south1 \
  --schedule="*/2 * * * *" --uri=<NEW_URL>/ingest/tick --http-method=POST \
  --oidc-service-account-email=<NEW_INVOKER_SA>
```
Repeat for `review-iq-digest-daily` (`0 2 * * *`, Asia/Kolkata, currently PAUSED — recreate
paused) and `review-iq-detector-sweep` (`0 */6 * * *`, currently PAUSED — recreate paused).

## 5. Killswitch Terraform (third re-point)

`ops/budget-killswitch/variables.tf` already targets `01285B-91E4CB-70AD7E` from the earlier
in-place migration — only the `project_id` variable needs to change to `reviewiq-prod-260813`.
Re-apply, verify in `dry_run=true` first, confirm the `AUTOMATED_KILLSWITCH_FIRED` log line
appears on a dry-run trigger, then flip to `dry_run=false`.

## 6. Frontend redeploys (backend URL doesn't change if DNS repoints the same domain)

If step 3's DNS repoint keeps `api.samidhareviews.xyz` as the backend URL, the 2 Vercel + 1
Cloudflare Pages frontends need **no code change** — only a redeploy is needed if any of them
cached a build-time reference to the old project's Cloud Run URL directly instead of the custom
domain. Grep each frontend's build output for `review-iq-prod` or any `*.run.app` literal before
assuming a redeploy isn't needed.

## 7. Verify before cutover

- Real login + real review-ingestion flow against the new project's `.web.app` URL, not just
  `/health`.
- Confirm killswitch dry-run log line fires correctly.
- Confirm all 3 scheduler jobs' next-run times look correct and (for the paused two) stay paused.
- Only then: repoint `api.samidhareviews.xyz` DNS, wait for cert, re-verify against the custom
  domain, then decommission `review-iq-prod`.
