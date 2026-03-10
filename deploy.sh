#!/usr/bin/env bash
# LiveNotebookLM - Deploy to Google Cloud Run
# Usage: ./deploy.sh [PROJECT_ID] [REGION]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-}}"
REGION="${2:-us-central1}"

SERVICE_NAME="live-notebook-lm"
REPO_NAME="live-notebook-lm"
IMAGE_NAME="live-notebook-lm"
BUCKET_SUFFIX="livenotebooklm-dev"
SERVICE_ACCOUNT_EMAIL="live-notebooklm-sa@${PROJECT_ID}.iam.gserviceaccount.com"
AGENT_MODEL="gemini-live-2.5-flash-native-audio"

TAG="${TAG:-$(date +%Y%m%d-%H%M%S)}"

if [[ -z "${PROJECT_ID}" ]]; then
  echo "Usage: ./deploy.sh PROJECT_ID [REGION]"
  echo "Or set GOOGLE_CLOUD_PROJECT"
  exit 1
fi

ARTIFACT_REGISTRY="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}"
FULL_IMAGE="${ARTIFACT_REGISTRY}/${IMAGE_NAME}:${TAG}"

echo "=== LiveNotebookLM Deploy ==="
echo "Project:            ${PROJECT_ID}"
echo "Region:             ${REGION}"
echo "Service name:       ${SERVICE_NAME}"
echo "Artifact Registry:  ${ARTIFACT_REGISTRY}"
echo "Image:              ${FULL_IMAGE}"
echo "Service account:    ${SERVICE_ACCOUNT_EMAIL}"
echo "Bucket:             ${PROJECT_ID}-${BUCKET_SUFFIX}"
echo "Model:              ${AGENT_MODEL}"
echo ""

# Step 1: Terraform init & apply with placeholder image
echo ">>> Step 1: Terraform init & apply (placeholder image)"
cd "${SCRIPT_DIR}/terraform"

terraform init

terraform apply -auto-approve \
  -var="project_id=${PROJECT_ID}" \
  -var="region=${REGION}" \
  -var="service_name=${SERVICE_NAME}" \
  -var="artifact_registry_repo=${REPO_NAME}" \
  -var="bucket_suffix=${BUCKET_SUFFIX}" \
  -var="service_account_email=${SERVICE_ACCOUNT_EMAIL}" \
  -var="agent_model=${AGENT_MODEL}" \
  -var="image=gcr.io/cloudrun/hello"

# Step 2: Build and push container image
echo ""
echo ">>> Step 2: Build & push container image"
cd "${SCRIPT_DIR}"

gcloud builds submit \
  --tag "${FULL_IMAGE}" \
  --project "${PROJECT_ID}" \
  .

# Step 3: Terraform apply with real image
echo ""
echo ">>> Step 3: Update Cloud Run with real image"
terraform -chdir="${SCRIPT_DIR}/terraform" apply -auto-approve \
  -var="project_id=${PROJECT_ID}" \
  -var="region=${REGION}" \
  -var="service_name=${SERVICE_NAME}" \
  -var="artifact_registry_repo=${REPO_NAME}" \
  -var="bucket_suffix=${BUCKET_SUFFIX}" \
  -var="service_account_email=${SERVICE_ACCOUNT_EMAIL}" \
  -var="agent_model=${AGENT_MODEL}" \
  -var="image=${FULL_IMAGE}"

echo ""
echo "=== Deploy complete ==="

echo "Cloud Run URL:"
terraform -chdir="${SCRIPT_DIR}/terraform" output -raw cloud_run_url 2>/dev/null || true