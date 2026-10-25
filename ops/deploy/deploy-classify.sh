#!/usr/bin/env bash
# Build (in Cloud Build) and deploy the PRIVATE Cloud Run service that serves POST /v2/classify (S26 sizing decisions, S27 auth decision,
# S29: Cloud Build instead of a local Docker build): own service, 2 GiB, 1 vCPU, min-instances 0, max-instances 2, model.onnx baked into
# the image, hash-pinned requirements, NO unauthenticated access, NO secrets and NO database credential. Only the review-iq service
# account may invoke it (roles/run.invoker). NOT run by CI. The existing `review-iq` service is never modified.
#
#   bash ops/deploy/deploy-classify.sh /path/to/classify_artifacts   # dir with model.onnx and hf/tokenizer.json
#
# Guards (each stops the script before anything is built, pushed or deployed):
#   - the working tree is clean and HEAD is an ancestor of origin/main (CLAUDE.md 31a: production deploys come from merged code);
#   - model.onnx and tokenizer.json match the sha256 pins below (weights are not in git; the pin is the delivery check);
#   - CONFIRM_REGISTRY_COST=yes: the image adds its size to Artifact Registry, billed above the 0.5 GB free tier (about $0.10 per
#     GB-month, BELIEVED). S29 measured 481.5 MB of unique layers, about 4 cents a month in total after the push.
# The build runs in Cloud Build (default e2-standard-2 pool): about 2 minutes, inside the free allowance of 2,500 build-minutes a month
# per billing account (Google's pricing page, S29). The source upload (git archive of HEAD + the two weight files, about 375 MB) goes
# to the project's Cloud Build bucket and is DELETED by this script afterwards (a US multi-region bucket is not in the free storage tier).
# After the deploy it ASSERTS the access model: the IAM policy holds exactly one invoker (the review-iq service account), none of
# allUsers / allAuthenticatedUsers, and an unauthenticated request is refused with 401/403. It prints no secret value.
# Rollback: `gcloud run services delete review-iq-classify --region=asia-south1` (nothing else depends on it).
set -euo pipefail

ACCT="gaurav.gandhi1129@gmail.com"
PROJECT="reviewiq-prod-260813"
REGION="asia-south1"
AR_REPO="cloud-run-source-deploy"
SERVICE="review-iq-classify"
SA_NAME="review-iq-classify"
SA="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
CALLER_SA="review-iq-runner@${PROJECT}.iam.gserviceaccount.com" # the serving service account of `review-iq`
STAGING_BUCKET="gs://${PROJECT}_cloudbuild"
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
[ "${CONFIRM_REGISTRY_COST:-}" = "yes" ] || { echo "stop: set CONFIRM_REGISTRY_COST=yes to build and push (billed registry storage)"; exit 2; }

IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}/${SERVICE}:${TAG}"
echo "== stage the build context (git archive of HEAD + the two pinned weight files)"
STAGE="$(mktemp -d)"
trap 'rm -rf "${STAGE}"' EXIT
git archive HEAD Dockerfile.classify pyproject.toml uv.lock requirements-classify.txt app engine/__init__.py engine/intent/__init__.py engine/intent/decide.py | tar -x -C "${STAGE}"
mkdir -p "${STAGE}/.artifacts/hf"
cp "${ARTIFACTS}/model.onnx" "${STAGE}/.artifacts/model.onnx"
cp "${ARTIFACTS}/hf/tokenizer.json" "${STAGE}/.artifacts/hf/tokenizer.json"
# Cloud Build's docker client has no `--build-context`; Dockerfile.classify's `COPY --from=artifacts` resolves to a LOCAL IMAGE named
# `artifacts` under the classic builder, so step 1 builds that image from the two weight files.
cat > "${STAGE}/cloudbuild.classify.yaml" <<'EOF'
steps:
  - name: gcr.io/cloud-builders/docker
    entrypoint: bash
    args:
      - -c
      - |
        echo "FROM scratch" > Dockerfile.artifacts
        echo "COPY .artifacts/ /" >> Dockerfile.artifacts
        docker build -f Dockerfile.artifacts -t artifacts .
  - name: gcr.io/cloud-builders/docker
    args: ["build", "-f", "Dockerfile.classify", "-t", "${_IMAGE}", "."]
images: ["${_IMAGE}"]
options:
  logging: CLOUD_LOGGING_ONLY
timeout: 1200s
EOF

echo "== build in Cloud Build and push ${IMAGE##*/}"
SRC_DIR="${STAGING_BUCKET}/classify-src-${TAG}"
g builds submit "${STAGE}" --config="${STAGE}/cloudbuild.classify.yaml" --substitutions="_IMAGE=${IMAGE}" \
  --region="${REGION}" --gcs-source-staging-dir="${SRC_DIR}"
echo "== delete the uploaded build source (weights) from ${SRC_DIR}"
g storage rm --recursive "${SRC_DIR}" || echo "warning: could not delete ${SRC_DIR}; delete it by hand"
DIGEST="$(g artifacts docker images describe "${IMAGE}" --format='value(image_summary.digest)')"
[ -n "${DIGEST}" ] || { echo "FAIL: no digest for ${IMAGE}"; exit 1; }

echo "== service account (no roles, no secrets)"
g iam service-accounts describe "${SA}" >/dev/null 2>&1 || g iam service-accounts create "${SA_NAME}" --display-name="review-iq classify service (no roles, no secrets)"

echo "== deploy (private, 2Gi, 1 vCPU, scale to zero, max 2 instances)"
g run deploy "${SERVICE}" --image="${IMAGE%:*}@${DIGEST}" --region="${REGION}" --service-account="${SA}" \
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
echo "next (the project Owner can invoke without a binding; no --audiences for a user account):"
echo "      CLASSIFY_ID_TOKEN=\$(gcloud auth print-identity-token --account=${ACCT}) \\"
echo "      PYTHONPATH=. python scripts/classify_live_parity.py --url ${URL} --texts <scale.jsonl> --out <result.json>"
