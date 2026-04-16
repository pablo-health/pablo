#!/usr/bin/env bash
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
#
# Setup Cloud Tasks queue and service account for AssemblyAI transcription
# polling. The backend enqueues a Cloud Task after submitting audio to
# AssemblyAI; the task polls for completion and triggers SOAP generation.
#
# Creates:
#   - Service account: cloud-tasks-invoker (shared across all Cloud Tasks)
#   - Cloud Tasks queue: pablo-transcription (for transcription polling)
#   - IAM bindings for enqueuing and invoking
#
# Idempotent — safe to run multiple times.
#
# Usage:
#   GCP_PROJECT_ID=pablohealth-dev  ./scripts/setup-transcription-queue.sh
#   GCP_PROJECT_ID=pablohealth-prod ./scripts/setup-transcription-queue.sh

set -euo pipefail

# --- Configuration ---
PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID}"
REGION="${GCP_REGION:-us-central1}"

SA_NAME="cloud-tasks-invoker"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
QUEUE_NAME="pablo-transcription"

echo "=== Pablo Transcription Queue Setup ==="
echo "Project:  ${PROJECT_ID}"
echo "Region:   ${REGION}"
echo "Queue:    ${QUEUE_NAME}"
echo "SA:       ${SA_EMAIL}"
echo ""

# --- 1. Enable Cloud Tasks API ---
echo "--- Enabling Cloud Tasks API ---"
gcloud services enable cloudtasks.googleapis.com \
    --project="${PROJECT_ID}" --quiet
echo ""

# --- 2. Service Account ---
echo "--- Creating service account ---"
if gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT_ID}" &>/dev/null; then
    echo "Service account ${SA_EMAIL} already exists"
else
    gcloud iam service-accounts create "${SA_NAME}" \
        --display-name="Cloud Tasks Invoker (transcription, shared)" \
        --project="${PROJECT_ID}" --quiet
    echo "Created ${SA_EMAIL}"
fi
echo ""

# --- 3. Grant Cloud Run invoker role ---
echo "--- Granting Cloud Run invoker role ---"
gcloud run services add-iam-policy-binding pablo-backend \
    --region="${REGION}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/run.invoker" \
    --project="${PROJECT_ID}" --quiet 2>/dev/null || true
echo "Granted roles/run.invoker on pablo-backend"
echo ""

# --- 4. Create Cloud Tasks queue ---
echo "--- Creating Cloud Tasks queue ---"
if gcloud tasks queues describe "${QUEUE_NAME}" --location="${REGION}" --project="${PROJECT_ID}" &>/dev/null; then
    echo "Queue ${QUEUE_NAME} already exists"
else
    gcloud tasks queues create "${QUEUE_NAME}" \
        --location="${REGION}" \
        --max-dispatches-per-second=5 \
        --max-concurrent-dispatches=10 \
        --max-attempts=5 \
        --min-backoff=10s \
        --max-backoff=600s \
        --project="${PROJECT_ID}" --quiet
    echo "Created queue ${QUEUE_NAME}"
fi
echo ""

# --- 5. Grant enqueuer role on queue ---
echo "--- Granting enqueuer permissions ---"

# The invoker SA needs to be able to enqueue (for self-re-enqueue during polling)
gcloud tasks queues add-iam-policy-binding "${QUEUE_NAME}" \
    --location="${REGION}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/cloudtasks.enqueuer" \
    --project="${PROJECT_ID}" --quiet 2>/dev/null || true
echo "Granted roles/cloudtasks.enqueuer to ${SA_NAME} on ${QUEUE_NAME}"

# Backend's Cloud Run SA needs enqueuer too (initial enqueue from upload handler)
BACKEND_SA="$(gcloud run services describe pablo-backend \
    --region="${REGION}" --project="${PROJECT_ID}" \
    --format='value(spec.template.spec.serviceAccountName)' 2>/dev/null || echo "")"
if [ -n "${BACKEND_SA}" ]; then
    gcloud tasks queues add-iam-policy-binding "${QUEUE_NAME}" \
        --location="${REGION}" \
        --member="serviceAccount:${BACKEND_SA}" \
        --role="roles/cloudtasks.enqueuer" \
        --project="${PROJECT_ID}" --quiet 2>/dev/null || true
    echo "Granted roles/cloudtasks.enqueuer to backend SA (${BACKEND_SA})"
else
    echo "WARNING: Could not detect backend service account."
    echo "Manually grant roles/cloudtasks.enqueuer on ${QUEUE_NAME} to the backend SA."
fi
echo ""

echo "=== Setup Complete ==="
echo ""
echo "Queue '${QUEUE_NAME}' is ready for transcription polling."
echo ""
echo "Run for each environment:"
echo "  Dev:  GCP_PROJECT_ID=pablohealth-dev  $0"
echo "  Prod: GCP_PROJECT_ID=pablohealth-prod $0"
