#!/usr/bin/env bash
# Build and deploy the PRIVATE Cloud Run service that serves POST /v2/classify (S26 sizing decisions, S27 auth decision):
# own service, 2 GiB, 1 vCPU, min-instances 0, model.onnx baked into the image, hash-pinned requirements, NO unauthenticated access,
# NO secrets and NO database credential. Only the review-iq service account may invoke it (roles/run.invoker). NOT run by CI.
# The existing `review-iq` service is never modified.
#
#   bash ops/deploy/deploy-classify.sh /path/to/classify_artifacts   # dir with model.onnx and hf/tokenizer.json
#
# Guards (each stops the script before anything is built, pushed or deployed):
#   - the working tree is clean and HEAD is an ancestor of origin/main (CLAUDE.md 31a: production deploys come from merged code);
#   - model.onnx and tokenizer.json match the sha256 pins below (weights are not in git; the pin is the delivery check);
#   - CONFIRM_REGISTRY_COST=yes: the image adds its size to Artifact Registry, billed above the 0.5 GB free tier (about $0.10 per
#     GB-month, BELIEVED). The script prints the figure; it does not decide for you.
# After the deploy it ASSERTS the access model: the IAM policy holds exactly one invoker (the review-iq service account), none of
# allUsers / allAuthenticatedUsers, and an unauthenticated request is refused with 401/403. It prints no secret value.
# Rollback: `gcloud run services delete review-iq-classify` (nothing else depends on it).
set -euo pipefail

ACCT="gaurav.gandhi1129@gmail.com"
PROJECT="reviewiq-prod-260813"
REGION="asia-south1"
AR_REPO="cloud-run-source-deploy"
SERVICE="review-iq-classify"
SA_NAME="review-iq-classify"
SA="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
CALLER_SA="review-iq-runner@${PROJECT}.iam.gserviceaccount.com" # the serving service account of `review-iq`
MODEL_SHA256="33158169d014fb424b1284dd32474863a62503530b618e4bf3db39210044b414"
VOCAB_FILE_SHA256="62c24cdc13d4c9952d63718d6c9fa4c287974249e16b7ade6d5a85e7bbb75626"
ARTIFACTS="${1:?usage: deploy-classify.sh <artifacts dir with model.onnx and hf/tokenizer.json>}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/../.." && pwd)"
cd "${REPO}"

g() { gcloud "$@" --account="${ACCT}" --project="${PROJECT}"; }

echo "== guards"
git fetch -q origin main
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "tree not clean"; exit 1; }
git merge-base --is-ancestor HEAD origin/main || { echo "HEAD is not on origin/main: deploy merged code only"; exit 1; }
TAG="$(git rev-parse --short=12 HEAD)"
[ "$(sha256sum "${ARTIFACTS}/model.onnx" | cut -d' ' -f1)" = "${MODEL_SHA256}" ] || { echo "model.onnx sha256 does not match the pin"; exit 1; }
[ "$(sha256sum "${ARTIFACTS}/hf/tokenizer.json" | cut -d' ' -f1)" = "${VOCAB_FILE_SHA256}" ] || { echo "tokenizer.json sha256 does not match the pin"; exit 1; }

IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}/${SERVICE}:${TAG}"
echo "== build ${IMAGE##*/}"
docker build -f Dockerfile.classify -t "${IMAGE}" --build-context "artifacts=${ARTIFACTS}" .
SIZE_MB=$(( $(docker image inspect "${IMAGE}" --format '{{.Size}}') / 1000000 ))
echo "image size (uncompressed) ${SIZE_MB} MB; the registry stores it compressed, billed above the 0.5 GB free tier"
[ "${CONFIRM_REGISTRY_COST:-}" = "yes" ] || { echo "stop: set CONFIRM_REGISTRY_COST=yes to push (billed storage)"; exit 2; }

echo "== push"
g auth print-access-token | docker login -u oauth2accesstoken --password-stdin "${REGION}-docker.pkg.dev" >/dev/null
docker push "${IMAGE}" >/dev/null

echo "== service account (no roles, no secrets)"
g iam service-accounts describe "${SA}" >/dev/null 2>&1 || g iam service-accounts create "${SA_NAME}" --display-name="review-iq classify service (no roles, no secrets)"

echo "== deploy (private, 2Gi, 1 vCPU, scale to zero)"
g run deploy "${SERVICE}" --image="${IMAGE}" --region="${REGION}" --service-account="${SA}" \
  --cpu=1 --memory=2Gi --min-instances=0 --max-instances=2 --concurrency=4 --timeout=60 --cpu-boost \
  --no-allow-unauthenticated

echo "== invoker: ${CALLER_SA} only"
g run services add-iam-policy-binding "${SERVICE}" --region="${REGION}" \
  --member="serviceAccount:${CALLER_SA}" --role=roles/run.invoker >/dev/null

echo "== assert the access model"
POLICY="$(g run services get-iam-policy "${SERVICE}" --region="${REGION}" --format=json)"
if echo "${POLICY}" | grep -Eq 'allUsers|allAuthenticatedUsers'; then echo "FAIL: public principal in the IAM policy"; exit 3; fi
[ "$(echo "${POLICY}" | grep -c 'serviceAccount:')" = "1" ] || { echo "FAIL: expected exactly one service-account invoker"; exit 3; }
URL="$(g run services describe "${SERVICE}" --region="${REGION}" --format='value(status.url)')"
CODE="$(curl -s -o /dev/null -w '%{http_code}' -X POST "${URL}/v2/classify" -H 'Content-Type: application/json' -d '{"reviews":[{"text":"x"}]}')"
case "${CODE}" in 401 | 403) echo "ok: unauthenticated request refused (${CODE})" ;; *) echo "FAIL: unauthenticated request got ${CODE}"; exit 3 ;; esac

echo "deployed ${SERVICE}: ${URL}"
echo "next: CLASSIFY_ID_TOKEN=\$(gcloud auth print-identity-token --account=${ACCT} --audiences=${URL}) \\"
echo "      PYTHONPATH=. python scripts/classify_live_parity.py --url ${URL} --texts <scale.jsonl> --out reports/serving/classify_live_parity.json"
echo "(the token needs run.invoker: add your own account to the service first, or impersonate ${CALLER_SA})"
