# Failover probe (GCP-hosted)

Nightly proof that the production LLM failover works: the probe forces an invalid primary
key IN-PROCESS and requires `app.core.llm.extract_with_llm` to be served by the secondary
(Groq shared-account key, `openai/gpt-oss-120b`). No production key lives in GitHub (GG
decision A, S23); the job reads Secret Manager directly. It replaced the GitHub-hosted
`failover-probe.yml`.

## Pieces (project `reviewiq-prod-260813`, region `asia-south1`)

| Piece | Name | Notes |
| --- | --- | --- |
| Cloud Run Job | `failover-probe` | Same image as the serving revision; SA `failover-probe` |
| Job SA | `failover-probe@` | `secretAccessor` on `groq-api-key` ONLY. Never `groq-api-key-samidha` |
| Scheduler | `failover-probe-nightly` | `30 2 * * *` UTC, OAuth as `failover-probe-sched@`, `run.invoker` on the job only |
| Log metric | `failover_probe_failures` | ERROR logs of the job (stderr marker `FAILOVER_PROBE_FAILED`, `Container called exit(1)`) or of the scheduler job |
| Alert policy | `Failover probe failing` | metric > 0 -> the existing email channel (the one used by `Uptime failure - Samidha Reviews`) |

Everything is created/updated idempotently by `ops/probes/apply-failover-probe.sh`. Re-run it
after each deploy so the job follows the serving image.

## States

`PASS`, `FAIL` (exit 1, loud, key masked), `NOT_CONFIGURED` (exit 1 unless acknowledged;
the job does not acknowledge, so a missing secondary key fails the job).

## Run and inspect

```bash
# where `<owner-account>` is the project's sole GCP owner identity (substitute at run time)
A=--account=<owner-account>; P=--project=reviewiq-prod-260813
gcloud run jobs execute failover-probe $A $P --region=asia-south1 --wait
gcloud run jobs executions list --job=failover-probe $A $P --region=asia-south1 --limit=5
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="failover-probe"' $A $P --freshness=1d --order=asc --format='value(textPayload)'
```

A FAIL line carries the swallowed provider errors (`llm.secondary_failed: Error code: 429 ...`)
so the cause (quota vs key vs model) is readable without the app logs.

## Forced-failure drill (never touches review-iq)

```bash
gcloud run jobs execute failover-probe $A $P --region=asia-south1 \
  --update-env-vars=SECONDARY_PROVIDER_MODEL=invalid-model-forced-failure --wait
```

The override applies to that one execution only. Expected: the execution fails (exit 1), a
`FAILOVER_PROBE_FAILED` ERROR line is logged, the metric ticks and the alert policy opens an
alert. (`SECONDARY_PROVIDER_API_KEY` cannot be overridden this way: it is secret-backed.)

## Source of the probe script

`PROBE_SOURCE=inline` (default of the apply script): `scripts/` was not in the image, so the
script is passed base64 in the env var `PROBE_SRC_B64` and exec'd by `python -c`.
Once the Dockerfile line `COPY scripts/probe_failover.py` has shipped in a normal CI deploy,
run `PROBE_SOURCE=image bash ops/probes/apply-failover-probe.sh` to run it from the image.
Re-apply (either mode) whenever `scripts/probe_failover.py` changes.

## Cost and quota

One call of about 2K tokens per night on the Groq free tier, shared-account key: $0. The
shared key's daily token budget (200K TPD for the model) is shared with every other user of
that org, so a quota-exhausted night is a real FAIL, not noise: production failover would
429 too.

## Known gaps

- A job that cannot start at all (e.g. the secret accessor binding revoked) fails the
  execution but may log nothing at ERROR; the alert would not fire. Check
  `gcloud run jobs executions list` if the metric has been quiet and no one has seen a PASS.
- Email delivery of the alert has not been observed by the author; only the open alert is.
