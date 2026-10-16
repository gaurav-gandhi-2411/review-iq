# Uptime monitoring (Cloud Monitoring, as code)

Project `reviewiq-prod-260813`. Definitions live in `ops/monitoring/apply_monitoring.py`
(stdlib Python over the Monitoring v3 REST API; matched by `displayName`, so re-running
converges). It replaced a hand-made `api-health` check that existed only in the console
(and the GitHub Actions `uptime-alert.yml` poller, retired in the companion PR).

## What exists

| Resource | Definition | Fires when |
|---|---|---|
| Check `api-health-https` | `GET https://api.samidhareviews.xyz/health`, port 443, `validateSsl`, 2xx and body contains `"status":"ok"`, every 60 s, timeout 10 s, all six regions, failures logged | (it is the signal source) |
| Policy `Uptime failure - Samidha Reviews` | count of regions whose latest check failed, `> 1` for 120 s (unchanged rule, retargeted to the https check) | about 2+ regions down together for 2 minutes |
| Policy `Uptime failure rate - Samidha Reviews` | pooled pass-fraction over a trailing 900 s < 0.97 | about 3+ failed checks (any regions) within 15 minutes |
| Policy `Uptime TLS certificate expiry - Samidha Reviews` | `uptime_check/time_until_ssl_cert_expires` < 14 days | certificate has under 14 days left, or fails validation (no series then, see gaps) |

All three route to the project's one existing enabled email notification channel (looked up
at apply time; the script refuses to guess if there is not exactly one). No channel is created.

Free-tier footprint: one uptime check, 6 regions x 1/min = about 259k executions/month,
under the 1M/month free allotment. Two checks coexisted only during migration; the
temporary alert test added a third for a few minutes.

## Why these thresholds (replay of real data)

Source: raw `uptime_check/check_passed` for the old `api-health` check, window 2026-10-09 00:00Z to
2026-10-12 05:00Z, read-only (`ops/monitoring/replay_failure_rate.py` reproduces the table
from a dump of that series; the repo does not store the dump). Monitoring samples every 10 s
per region, so one failed check is a run of about 6 false points. 21 failed checks in
the window; from 2026-10-10 onward they form 10 bursts:

| Burst (UTC) | Failed checks | Cause seen | Fires 2-region/120 s? | Fires rate policy (>=3 / 15 min)? |
|---|---|---|---|---|
| 10-10 06:35 | 1 (oregon) | Cloud Run 503 | no | no |
| 10-10 07:46-07:48 | 4 (oregon, then virginia, sao paulo, singapore) | Cloud Run 503 | no (each region failed one check) | YES |
| 10-10 10:32 | 1 (virginia) | probe-side `REQUEST_EXCEPTION` | no | no |
| 10-10 11:09 | 3 (sao paulo, virginia, singapore) | Cloud Run 503 | no | YES |
| 10-10 16:21 | 1 (belgium) | `REQUEST_EXCEPTION` | no | no |
| 10-10 21:59 | 1 (belgium) | `REQUEST_EXCEPTION` | no | no |
| 10-11 06:33 | 1 (oregon) | Cloud Run 503 | no | no |
| 10-11 06:48 | 1 (oregon) | Cloud Run 503 | no | no (2 in 15 min at most) |
| 10-11 17:19 | 1 (belgium) | `REQUEST_EXCEPTION` | no | no |
| 10-12 00:58 | 1 (virginia) | `REQUEST_EXCEPTION` | no | no |

Result: the rate policy opens 2 incidents in about 2.2 days; the 5 isolated probe-side
`REQUEST_EXCEPTION` blips (and the 6 more on 10-09, all from eur-belgium) never fire it.
Sweep: window 15 min with threshold 2, 3 or 4 gave 2, 2, 1 episodes; 10 and 30 min windows
at threshold 3 also gave 2. Threshold 4 would miss the 3-check 11:09 burst. A pooled
pass-fraction rule of `< 0.99` (about 1 failed check) would have opened 15 incidents in the
window, i.e. paged on every blip. `< 0.97` was also replayed over the raw points (sliding
900 s, 60 s steps): 2 episodes, at 07:49 and 11:10 on 10-10, matching the count rule.
Separately, Cloud Run logged 503s to the uptime-check user agent (for example 10-09 13:51,
15:23, 18:33 and 10-10 10:56, 16:20) that have no failed-check sample at all, so no
check-based policy can see them (mechanism not established).

## Applying / re-applying

```bash
python ops/monitoring/apply_monitoring.py --account=<owner-account> plan
python ops/monitoring/apply_monitoring.py --account=<owner-account> apply --only checks
# confirm the check passes from all six regions (see Verifying), THEN:
python ops/monitoring/apply_monitoring.py --account=<owner-account> apply --only policies
```

`--account` is mandatory on purpose (the gcloud config is machine-global). The policies step
refuses to run if the https check does not exist yet. Re-running is a no-op apart from the
update call.

## Verifying

```bash
gcloud monitoring uptime list-configs --account=<acct> --project=reviewiq-prod-260813 \
  --format="table(displayName,period,httpCheck.port,httpCheck.useSsl,httpCheck.validateSsl)"
gcloud monitoring policies list --account=<acct> --project=reviewiq-prod-260813 \
  --format="table(displayName,enabled)"
```

Per-region pass state: Monitoring API `timeSeries.list` on
`monitoring.googleapis.com/uptime_check/check_passed` filtered by `metric.label.check_id`.

## Testing the email route

```bash
python ops/monitoring/apply_monitoring.py --account=<acct> test-alert up      # temp check + policy
# poll: GET https://monitoring.googleapis.com/v3/projects/<p>/alerts?filter=policy.name="<policy>"
python ops/monitoring/apply_monitoring.py --account=<acct> test-alert down            # lists only
python ops/monitoring/apply_monitoring.py --account=<acct> test-alert down --confirm  # deletes
```

`down` only matches resources named `s23-alert-test` and fails on an empty match.

Run of 2026-10-12: policy created 07:41:08Z; the Alerts API showed alert
`0.odqcanw66q68` for it `state: OPEN`, `openTime 2026-10-12T07:45:30Z` (about 4m22s later),
all six regions reporting `check_passed=false`. This proves the policy fired. It does not
prove delivery: the Alerts API exposes no notification status and no notification log
entries were found, and receipt in the inbox can only be confirmed by a human reading the
mailbox. The temporary check and policy were then deleted; the alert read back `CLOSED` at 07:47:26Z.

## Known gaps

- A failure in a single region never alerts (the availability policy needs more than one
  region; the rate policy needs about 3 failed checks). A real regional-only outage is silent.
- Email is a single route: one channel, one mailbox. No SMS/push/chat fallback, no
  second recipient. If that mailbox or provider is down, nothing pages.
- The certificate policy needs a `time_until_ssl_cert_expires` series. If TLS breaks outright
  (expired or invalid), the check fails validation: the availability and rate policies catch
  that, but the cert policy itself goes silent, it only warns while the cert is still valid.
- 503s that Monitoring does not count as a failed check (above) are invisible.
- `/health` proves the container and DB ping, not authenticated paths (see
  `web-surface-probe.yml`).

## Rollback

The old check was a plain-HTTP `api-health` (port 80, no SSL), recreate with
`check_body("api-health", "/health", '"status":"ok"', ssl=False)` from the script, then point
the availability policy's filter back at its `check_id`. Exact commands are in the PR body.
