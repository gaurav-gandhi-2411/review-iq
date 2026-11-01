#!/usr/bin/env bash
# Idempotent create/update of the GCP-hosted nightly failover probe (S23, GG decision A:
# no production key in GitHub; the probe reads Secret Manager directly).
#
#   Cloud Scheduler `failover-probe-nightly` (02:30 UTC)
#     -> run.googleapis.com jobs :run (OAuth as SA failover-probe-sched, run.invoker on the JOB only)
#       -> Cloud Run Job `failover-probe` (SA failover-probe, secretAccessor on `groq-api-key` ONLY)
#         -> scripts/probe_failover.py, PROBE_PATHS=failover: forces an invalid primary key
#            in-process and requires the production secondary to answer.
#     -> log-based metric `failover_probe_failures` -> alert policy -> the EXISTING email channel.
#
# Image: the SAME image the serving revision runs (read from `gcloud run services describe`;
# nothing is built or pushed, review-iq itself is never modified). Re-run this script after a
# deploy to move the job to the new image.
#
# PROBE_SOURCE=inline (default until the Dockerfile COPY line has shipped): scripts/ is not in
# the image, so the script is passed base64 in PROBE_SRC_B64 and exec'd by `python -c`.
# PROBE_SOURCE=image: runs /app/scripts/probe_failover.py from the image (use once deployed).
#
# NEVER mounts `groq-api-key-samidha` (the dedicated-org primary). Prints no secret values and
# no notification addresses.
#
# Usage: bash ops/probes/apply-failover-probe.sh
set -euo pipefail

ACCT="gaurav.gandhi1129@gmail.com"
PROJECT="reviewiq-prod-260813"
REGION="asia-south1"
SERVICE="review-iq"
JOB="failover-probe"
SCHED="failover-probe-nightly"
JOB_SA_NAME="failover-probe"
SCHED_SA_NAME="failover-probe-sched"
JOB_SA="${JOB_SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
SCHED_SA="${SCHED_SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
SECRET="groq-api-key" # shared-account key, production's SECONDARY_PROVIDER_API_KEY source
SECONDARY_MODEL="openai/gpt-oss-120b"
METRIC="failover_probe_failures"
POLICY_NAME="Failover probe failing"
UPTIME_POLICY_NAME="Uptime failure - Samidha Reviews" # its channel is the existing email channel
SOURCE="${PROBE_SOURCE:-inline}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/../.." && pwd)"

g() { gcloud "$@" --account="${ACCT}" --project="${PROJECT}"; }

echo "== service accounts"
for pair in "${JOB_SA_NAME}:Failover probe job (secretAccessor on ${SECRET} only)" \
  "${SCHED_SA_NAME}:Failover probe scheduler (run.invoker on the job only)"; do
  name="${pair%%:*}"
  if ! g iam service-accounts describe "${name}@${PROJECT}.iam.gserviceaccount.com" >/dev/null 2>&1; then
    g iam service-accounts create "${name}" --display-name="${pair#*:}"
  fi
done

echo "== secret accessor (job SA, ${SECRET} only)"
g secrets add-iam-policy-binding "${SECRET}" \
  --member="serviceAccount:${JOB_SA}" --role=roles/secretmanager.secretAccessor >/dev/null

echo "== image of the serving revision"
IMAGE="$(g run services describe "${SERVICE}" --region="${REGION}" \
  --format='value(spec.template.spec.containers[0].image)')"
echo "image: ${IMAGE}"

COMMON=(--region="${REGION}" --image="${IMAGE}" --service-account="${JOB_SA}"
  --max-retries=0 --task-timeout=120s --cpu=1 --memory=512Mi
  --set-secrets="SECONDARY_PROVIDER_API_KEY=${SECRET}:latest")
ENVVARS="SECONDARY_PROVIDER_KIND=groq,SECONDARY_PROVIDER_MODEL=${SECONDARY_MODEL},PROBE_PATHS=failover"

if [[ "${SOURCE}" == "image" ]]; then
  CMD=(--command=python --args="scripts/probe_failover.py")
  ENVS="${ENVVARS}"
else
  # Pass the script itself as a (non-secret) env var; `^@^` makes @ the list delimiter.
  B64="$(base64 -w0 "${REPO}/scripts/probe_failover.py" 2>/dev/null || base64 "${REPO}/scripts/probe_failover.py" | tr -d '\n')"
  ENVS="^@^${ENVVARS//,/@}@PROBE_SRC_B64=${B64}"
  CODE="import base64,os;p='/app/scripts/probe_failover.py';exec(compile(base64.b64decode(os.environ['PROBE_SRC_B64']),p,'exec'),{'__name__':'__main__','__file__':p})"
  CMD=(--command=python "--args=^@^-c@${CODE}")
fi

echo "== cloud run job (${SOURCE} mode)"
if g run jobs describe "${JOB}" --region="${REGION}" >/dev/null 2>&1; then
  g run jobs update "${JOB}" "${COMMON[@]}" "${CMD[@]}" --set-env-vars="${ENVS}"
else
  g run jobs create "${JOB}" "${COMMON[@]}" "${CMD[@]}" --set-env-vars="${ENVS}"
fi

echo "== run.invoker on the job only (scheduler SA)"
g run jobs add-iam-policy-binding "${JOB}" --region="${REGION}" \
  --member="serviceAccount:${SCHED_SA}" --role=roles/run.invoker >/dev/null

echo "== cloud scheduler"
URI="https://run.googleapis.com/v2/projects/${PROJECT}/locations/${REGION}/jobs/${JOB}:run"
SARGS=(--location="${REGION}" --schedule="30 2 * * *" --time-zone=Etc/UTC --uri="${URI}"
  --http-method=POST --oauth-service-account-email="${SCHED_SA}"
  --oauth-token-scope=https://www.googleapis.com/auth/cloud-platform)
if g scheduler jobs describe "${SCHED}" --location="${REGION}" >/dev/null 2>&1; then
  g scheduler jobs update http "${SCHED}" "${SARGS[@]}"
else
  g scheduler jobs create http "${SCHED}" "${SARGS[@]}"
fi

echo "== log-based metric ${METRIC}"
FILTER='(resource.type="cloud_run_job" AND resource.labels.job_name="'"${JOB}"'" AND severity>=ERROR) OR (resource.type="cloud_scheduler_job" AND resource.labels.job_id="'"${SCHED}"'" AND severity>=ERROR)'
MDESC="Failover probe job error logs (FAILOVER_PROBE_FAILED / container exit) or scheduler errors"
if g logging metrics describe "${METRIC}" >/dev/null 2>&1; then
  g logging metrics update "${METRIC}" --description="${MDESC}" --log-filter="${FILTER}"
else
  g logging metrics create "${METRIC}" --description="${MDESC}" --log-filter="${FILTER}"
fi

echo "== alert policy (existing email channel, discovered from the uptime policy; not printed)"
CHANNEL="$(g monitoring policies list --filter="displayName=\"${UPTIME_POLICY_NAME}\"" \
  --format='value(notificationChannels[0])')"
if [[ -z "${CHANNEL}" ]]; then
  echo "ERROR: could not discover the existing notification channel; refusing to create an unrouted alert." >&2
  exit 1
fi
TMP="$(mktemp)"
trap 'rm -f "${TMP}"' EXIT
sed -e "s#__CHANNEL__#${CHANNEL}#" -e "s#__METRIC__#${METRIC}#" -e "s#__NAME__#${POLICY_NAME}#" \
  "${HERE}/failover-probe-alert-policy.json" >"${TMP}"
EXISTING="$(g monitoring policies list --filter="displayName=\"${POLICY_NAME}\"" --format='value(name)')"
if [[ -n "${EXISTING}" ]]; then
  g monitoring policies update "${EXISTING}" --policy-from-file="${TMP}" >/dev/null
else
  g monitoring policies create --policy-from-file="${TMP}" >/dev/null
fi
echo "done. Run it now: gcloud run jobs execute ${JOB} --region=${REGION} --project=${PROJECT} --account=${ACCT} --wait"
