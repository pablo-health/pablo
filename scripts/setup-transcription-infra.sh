#!/usr/bin/env bash
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
#
# Setup GCP infrastructure for the transcription service (GCP Batch):
#   - GCS bucket (encrypted audio storage with 7-day lifecycle)
#   - Service account with required permissions
#   - Enable GCP Batch API
#   - Build and push worker container image
#
# GCP Batch handles all compute provisioning — no MIG, no autoscaler,
# no health checks. Each transcription job provisions a spot T4 GPU VM,
# runs Whisper in a container, and tears down. True scale-to-zero.
#
# Prerequisites: gcloud CLI authenticated, project set.
# Usage: ./scripts/setup-transcription-infra.sh

set -euo pipefail

# --- Configuration ---
PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID}"
REGION="${GCP_REGION:-us-central1}"
BACKEND_URL="${BACKEND_URL:?Set BACKEND_URL (e.g., https://pablo-backend-xxxxx-uc.a.run.app)}"

REGISTRY="${GCP_REGISTRY:-us-central1-docker.pkg.dev/${PROJECT_ID}/pablo}"
BUCKET_NAME="pablo-audio-${PROJECT_ID}"
SA_NAME="transcription-worker"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
IMAGE_NAME="${REGISTRY}/transcription"

echo "=== Pablo Transcription Infrastructure Setup (GCP Batch) ==="
echo "Project:   ${PROJECT_ID}"
echo "Region:    ${REGION}"
echo "Registry:  ${REGISTRY}"
echo "Backend:   ${BACKEND_URL}"
echo ""

# --- 1. Enable APIs ---
echo "--- Enabling required APIs ---"
gcloud services enable batch.googleapis.com \
    compute.googleapis.com \
    storage.googleapis.com \
    --project="${PROJECT_ID}" --quiet
echo ""

# --- 2. Service Account ---
echo "--- Creating service account ---"
if gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT_ID}" &>/dev/null; then
    echo "Service account ${SA_EMAIL} already exists"
else
    gcloud iam service-accounts create "${SA_NAME}" \
        --project="${PROJECT_ID}" \
        --display-name="Pablo Transcription Worker"
fi

# Grant permissions:
# - GCS read (download audio)
# - Cloud Run invoker (callback to backend)
# - Batch job runner (required by Batch)
# - Logging writer (for job logs)
for role in roles/storage.objectViewer roles/run.invoker roles/batch.agentReporter roles/logging.logWriter; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="${role}" \
        --condition=None --quiet 2>/dev/null
done

echo "Service account configured"
echo ""

# --- 3. GCS Bucket ---
echo "--- Creating GCS bucket ---"
if gsutil ls -b "gs://${BUCKET_NAME}" &>/dev/null; then
    echo "Bucket gs://${BUCKET_NAME} already exists"
else
    gsutil mb -p "${PROJECT_ID}" -l "${REGION}" -b on "gs://${BUCKET_NAME}"
fi

# Set 7-day lifecycle (auto-delete audio after processing)
cat > /tmp/lifecycle.json <<'LIFECYCLE'
{
  "rule": [
    {
      "action": {"type": "Delete"},
      "condition": {"age": 7}
    }
  ]
}
LIFECYCLE
gsutil lifecycle set /tmp/lifecycle.json "gs://${BUCKET_NAME}"
rm /tmp/lifecycle.json

echo "Bucket configured with 7-day lifecycle policy"
echo ""

# --- 4. Build & Push Worker Image ---
echo "--- Building worker container image ---"
if [[ -d "services/transcription" ]]; then
    echo "Building ${IMAGE_NAME}..."
    gcloud builds submit services/transcription/ \
        --tag="${IMAGE_NAME}:latest" \
        --project="${PROJECT_ID}" \
        --quiet
    echo "Image pushed: ${IMAGE_NAME}:latest"
else
    echo "WARNING: services/transcription/ not found. Skipping image build."
    echo "Run this script from the repo root, or build manually:"
    echo "  docker build -t ${IMAGE_NAME} services/transcription/"
    echo "  docker push ${IMAGE_NAME}"
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Infrastructure ready. GCP Batch provisions spot T4 GPU VMs"
echo "on-demand and tears them down. \$0 when idle."
echo ""
echo "Set these Cloud Run env vars on pablo-backend:"
echo ""
echo "  gcloud run services update pablo-backend \\"
echo "    --set-env-vars=TRANSCRIPTION_ENABLED=true \\"
echo "    --set-env-vars=TRANSCRIPTION_AUDIO_BUCKET=${BUCKET_NAME} \\"
echo "    --set-env-vars=TRANSCRIPTION_WORKER_IMAGE=${IMAGE_NAME}:latest \\"
echo "    --set-env-vars=TRANSCRIPTION_BACKEND_CALLBACK_URL=${BACKEND_URL} \\"
echo "    --set-env-vars=TRANSCRIPTION_QUEUE_LOCATION=${REGION} \\"
echo "    --region=${REGION} --project=${PROJECT_ID} --quiet"
echo ""
echo "Run this script for each environment:"
echo "  Dev:  GCP_PROJECT_ID=pablohealth-dev  BACKEND_URL=https://... $0"
echo "  Prod: GCP_PROJECT_ID=pablohealth-prod BACKEND_URL=https://... $0"
echo ""
echo "Cost estimate (spot T4):"
echo "  ~\$0.02/session (50-min audio, ~3 min GPU processing)"
echo "  \$0 when idle (true scale-to-zero)"
