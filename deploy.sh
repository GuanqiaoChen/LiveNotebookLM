#!/usr/bin/env bash
# LiveNotebookLM - Deploy to Google Cloud Run
# Prerequisites: gcloud CLI, Docker (or Cloud Build), Terraform
# Usage: ./deploy.sh [PROJECT_ID] [REGION]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-}}"
REGION="${2:-us-central1}"
IMAGE_NAME="live-notebook-lm"
TAG="${TAG:-$(date +%Y%m%d-%H%M%S)}"

if [[ -z "$PROJECT_ID" ]]; then
  echo "Usage: ./deploy.sh PROJECT_ID [REGION]"
  echo "  or set GOOGLE_CLOUD_PROJECT"
  exit 1
fi

ARTIFACT_REGISTRY="${REGION}-docker.pkg.dev/${PROJECT_ID}/live-notebook-lm"
FULL_IMAGE="${ARTIFACT_REGISTRY}/${IMAGE_NAME}:${TAG}"

echo "=== LiveNotebookLM Deploy ==="
echo "Project: $PROJECT_ID"
echo "Region:  $REGION"
echo "Image:   $FULL_IMAGE"
echo ""

# 1. Terraform: create infra (AR, GCS, Cloud Run with placeholder)
echo ">>> Step 1: Terraform init & apply"
cd "$SCRIPT_DIR/terraform"
terraform init
terraform apply -auto-approve \
  -var="project_id=$PROJECT_ID" \
  -var="region=$REGION" \
  -var="image=gcr.io/cloudrun/hello"

# 2. Build and push container
echo ""
echo ">>> Step 2: Build & push container"
cd "$SCRIPT_DIR"
gcloud builds submit --tag "$FULL_IMAGE" --project "$PROJECT_ID" .

# 3. Terraform: update Cloud Run with actual image
echo ""
echo ">>> Step 3: Update Cloud Run with new image"
terraform -chdir=terraform apply -auto-approve \
  -var="project_id=$PROJECT_ID" \
  -var="region=$REGION" \
  -var="image=${FULL_IMAGE}"

echo ""
echo "=== Deploy complete ==="
terraform -chdir=terraform output -raw cloud_run_url 2>/dev/null || true
