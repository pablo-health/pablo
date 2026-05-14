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

# Backend's Cloud Run SA needs enqueuer too (initial enqueue from upload handler).
# IMPORTANT: this auto-detects the *current* Cloud Run SA. If the backend ever
# migrates to a different SA (e.g. from compute-default to a dedicated one)
# this script must be re-run, otherwise enqueues will start failing with
# PERMISSION_DENIED in production. See bd issue THERAPY-ooow.
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

    # The enqueued Cloud Task carries an OIDC token signed by cloud-tasks-invoker.
    # To set that identity on the task, the backend SA must be allowed to
    # impersonate (actAs) the invoker SA. Without this, create_task fails with
    # PERMISSION_DENIED on iam.serviceAccounts.actAs.
    gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
        --member="serviceAccount:${BACKEND_SA}" \
        --role="roles/iam.serviceAccountUser" \
        --project="${PROJECT_ID}" --quiet >/dev/null 2>&1 || true
    echo "Granted roles/iam.serviceAccountUser on ${SA_NAME} to backend SA"
else
    echo "WARNING: Could not detect backend service account."
    echo "Manually grant roles/cloudtasks.enqueuer on ${QUEUE_NAME} AND"
    echo "roles/iam.serviceAccountUser on ${SA_EMAIL} to the backend SA."
fi
echo ""

# --- 6. Drift check: warn on stale SAs bound to the queue ---
echo "--- Checking for stale enqueuer bindings ---"
EXPECTED_SAS=(
    "serviceAccount:${SA_EMAIL}"
    "serviceAccount:${BACKEND_SA}"
)
BOUND_SAS="$(gcloud tasks queues get-iam-policy "${QUEUE_NAME}" \
    --location="${REGION}" --project="${PROJECT_ID}" \
    --flatten="bindings[].members" \
    --filter="bindings.role=roles/cloudtasks.enqueuer" \
    --format="value(bindings.members)" 2>/dev/null || echo "")"
STALE_FOUND=0
for sa in ${BOUND_SAS}; do
    skip=0
    for expected in "${EXPECTED_SAS[@]}"; do
        [ "${sa}" = "${expected}" ] && { skip=1; break; }
    done
    if [ "${skip}" -eq 0 ]; then
        echo "WARNING: stale enqueuer binding: ${sa}"
        echo "  (not the current backend SA or cloud-tasks-invoker; remove manually if unused)"
        STALE_FOUND=1
    fi
done
if [ "${STALE_FOUND}" -eq 0 ]; then
    echo "No stale bindings."
fi
echo ""

echo "=== Setup Complete ==="
echo ""
echo "Queue '${QUEUE_NAME}' is ready for transcription polling."
echo ""
echo "Run for each environment:"
echo "  Dev:  GCP_PROJECT_ID=pablohealth-dev  $0"
echo "  Prod: GCP_PROJECT_ID=pablohealth-prod $0"
echo "  OSS:  GCP_PROJECT_ID=pablohealth-oss  $0"
