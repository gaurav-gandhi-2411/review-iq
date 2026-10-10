# Weekly digest: enabling and scheduling (S20 M3b)

Status when this file was written: code merged behind a migration that a human applies; NO Cloud
Scheduler job exists for the weekly cadence and none was created by the PR that added this runbook.
Nothing below has been run.

## What it is

An org opts in per event type (`high_urgency`, `likely_fake`) with
`PUT /bff/alerts/preferences/{event_type}` body `{"enabled": true, "frequency": "weekly_digest"}`.
`POST /internal/digest/run?cadence=weekly` (same `X-Digest-Trigger-Token` header and auth as the
daily call) then sends ONE email per org: week-over-week counts (this 7 days vs the 7 before), top
complaint topics, the pending urgent / likely-fake items (first 10, then "and N more"), a dashboard
link, and the unsubscribe footer plus `List-Unsubscribe` headers (same as the daily digest).
`POST /internal/digest/run` with no `cadence` is the daily digest, unchanged.

Behaviour worth knowing:

- No pending events means no email (the comparison section alone never triggers a send).
- An org whose notification email was cleared (unsubscribed) is skipped; its events stay pending.
- alert_log rows are written only after a successful send, tagged `details.cadence = "weekly"`
  (the per-cadence watermark). A re-run right after a successful send finds nothing to send.
- One org failing is logged and listed under `failed_orgs`; the rest still run.
- An event type is either daily or weekly per org, never both.

## Order of operations (human steps)

1. Merge the PR.
2. Apply migration `supabase/migrations/20261009000003_alert_preferences_weekly_digest.sql`
   via the normal `supabase/push.py` path. Until then, saving `weekly_digest` fails with a
   database constraint error and the weekly sweep errors out (fail closed); daily is unaffected.
3. Set `DASHBOARD_URL` on the Cloud Run service to the web app origin (for example the Vercel app
   origin; confirm the real value, it is not defined anywhere in this repo as a setting). If unset,
   the email omits the dashboard link instead of guessing one.
   `gcloud run services update review-iq --update-env-vars=DASHBOARD_URL=<origin>`
4. Create the scheduler job below (paused until step 5 is satisfied).
5. Confirm delivery readiness: the sender domain / `RESEND_FROM_EMAIL` question in
   `docs/email-deliverability-runbook.md` (sandbox sender only delivers to the Resend account
   owner), and the Resend free-tier cap of 100 per day shared with leads and alerts.

## Scheduler job to create (NOT created)

Mirror the existing `review-iq-digest-daily` job (inspect it first so the URI, OIDC service
account and token header match exactly):

```
gcloud scheduler jobs describe review-iq-digest-daily \
  --project=reviewiq-prod-260813 --location=asia-south1 \
  --format="yaml(httpTarget,schedule,timeZone)"
```

Then create the weekly job, Monday 02:00 Asia/Kolkata. Copy `<URI>`, `<INVOKER_SA>` and the header
value from the describe output above; the only differences from the daily job are the name, the
cron and the `?cadence=weekly` query string:

```
gcloud scheduler jobs create http review-iq-digest-weekly \
  --project=reviewiq-prod-260813 --location=asia-south1 \
  --schedule="0 2 * * 1" --time-zone="Asia/Kolkata" \
  --uri="<DIGEST_RUN_URI>?cadence=weekly" --http-method=POST \
  --headers="X-Digest-Trigger-Token=<DIGEST_TRIGGER_TOKEN>" \
  --oidc-service-account-email=<INVOKER_SA>
gcloud scheduler jobs pause review-iq-digest-weekly \
  --project=reviewiq-prod-260813 --location=asia-south1
```

Add `review-iq-digest-weekly` to `JOBS=(...)` in `scripts/toggle_scheduler_jobs.sh` when the job is
created so pause/resume/status cover it (not done in the code PR, the job does not exist yet).
It shares `DIGEST_TRIGGER_TOKEN` with the daily job, so token rotation (see `secret-rotation.md`)
must update both jobs' headers in the same pass.

## Verify after the first run

- Cloud Scheduler job status is success and the response body shows `"cadence": "weekly"`.
- `sent_per_org` and `failed_orgs` in that body; an empty `failed_orgs` is the pass condition.
- A second manual call immediately after must report 0 events per org (idempotence).
- Rollback: pause the job, then set affected prefs back to `immediate` or `daily_digest`. The
  migration is expand-only and needs no rollback for the app to keep working.
