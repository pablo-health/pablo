#!/usr/bin/env bash
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
#
# Setup Cloud Tasks queues and the shared service account for the backend's
# off-request jobs. Two queues share one invoker identity:
#   - pablo-transcription:   polls AssemblyAI after an audio upload.
#   - pablo-soap-generation: runs SOAP note generation off the upload request
#                            thread (the upload returns 202 and enqueues here).
#
# Creates / ensures:
#   - Service account: cloud-tasks-invoker (shared across all Cloud Tasks)
#   - Both Cloud Tasks queues above
#   - IAM bindings for enqueuing (backend SA + invoker SA) and invoking
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
# All queues the backend enqueues to. Same SA + backend-service + IAM pattern.
QUEUE_NAMES=("pablo-transcription" "pablo-soap-generation")

echo "=== Pablo Cloud Tasks Queue Setup ==="
echo "Project:  ${PROJECT_ID}"
echo "Region:   ${REGION}"
echo "Queues:   ${QUEUE_NAMES[*]}"
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

# Backend's Cloud Run SA needs enqueuer (initial enqueue from the route
# handlers). IMPORTANT: this auto-detects the *current* Cloud Run SA. If the
# backend ever migrates to a different SA (e.g. from compute-default to a
# dedicated one) this script must be re-run, otherwise enqueues will start
# failing with PERMISSION_DENIED in production. See bd issue THERAPY-ooow.
BACKEND_SA="$(gcloud run services describe pablo-backend \
    --region="${REGION}" --project="${PROJECT_ID}" \
    --format='value(spec.template.spec.serviceAccountName)' 2>/dev/null || echo "")"

# The enqueued Cloud Task carries an OIDC token signed by cloud-tasks-invoker.
# To set that identity, the backend SA must be allowed to impersonate (actAs)
# the invoker SA. Without this, create_task fails with PERMISSION_DENIED on
# iam.serviceAccounts.actAs. This is per-SA (not per-queue), so do it once.
if [ -n "${BACKEND_SA}" ]; then
    gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
        --member="serviceAccount:${BACKEND_SA}" \
        --role="roles/iam.serviceAccountUser" \
        --project="${PROJECT_ID}" --quiet >/dev/null 2>&1 || true
    echo "Granted roles/iam.serviceAccountUser on ${SA_NAME} to backend SA (${BACKEND_SA})"
else
    echo "WARNING: Could not detect backend service account."
    echo "Manually grant roles/iam.serviceAccountUser on ${SA_EMAIL} to the backend SA,"
    echo "and roles/cloudtasks.enqueuer on each queue below."
fi
echo ""

# --- 4–6. Per-queue: create, grant enqueuer, drift-check ---
setup_queue() {
    local queue_name="$1"
    echo "--- Queue: ${queue_name} ---"

    if gcloud tasks queues describe "${queue_name}" --location="${REGION}" --project="${PROJECT_ID}" &>/dev/null; then
        echo "  exists"
    else
        gcloud tasks queues create "${queue_name}" \
            --location="${REGION}" \
            --max-dispatches-per-second=5 \
            --max-concurrent-dispatches=10 \
            --max-attempts=5 \
            --min-backoff=10s \
            --max-backoff=600s \
            --project="${PROJECT_ID}" --quiet
        echo "  created"
    fi

    # The invoker SA can enqueue (self-re-enqueue, e.g. transcription polling).
    gcloud tasks queues add-iam-policy-binding "${queue_name}" \
        --location="${REGION}" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="roles/cloudtasks.enqueuer" \
        --project="${PROJECT_ID}" --quiet 2>/dev/null || true
    echo "  granted enqueuer to ${SA_NAME}"

    if [ -n "${BACKEND_SA}" ]; then
        gcloud tasks queues add-iam-policy-binding "${queue_name}" \
            --location="${REGION}" \
            --member="serviceAccount:${BACKEND_SA}" \
            --role="roles/cloudtasks.enqueuer" \
            --project="${PROJECT_ID}" --quiet 2>/dev/null || true
        echo "  granted enqueuer to backend SA"
    fi

    # Drift check: warn on enqueuer bindings that aren't the expected SAs.
    local bound_sas
    bound_sas="$(gcloud tasks queues get-iam-policy "${queue_name}" \
        --location="${REGION}" --project="${PROJECT_ID}" \
        --flatten="bindings[].members" \
        --filter="bindings.role=roles/cloudtasks.enqueuer" \
        --format="value(bindings.members)" 2>/dev/null || echo "")"
    local sa stale=0
    for sa in ${bound_sas}; do
        if [ "${sa}" != "serviceAccount:${SA_EMAIL}" ] && [ "${sa}" != "serviceAccount:${BACKEND_SA}" ]; then
            echo "  WARNING: stale enqueuer binding: ${sa} (remove manually if unused)"
            stale=1
        fi
    done
    [ "${stale}" -eq 0 ] && echo "  no stale bindings"
    echo ""
}

for q in "${QUEUE_NAMES[@]}"; do
    setup_queue "${q}"
done

echo "=== Setup Complete ==="
echo ""
echo "Queues ready: ${QUEUE_NAMES[*]}"
echo ""
echo "Run for each environment:"
echo "  Dev:  GCP_PROJECT_ID=pablohealth-dev  $0"
echo "  Prod: GCP_PROJECT_ID=pablohealth-prod $0"
echo "  OSS:  GCP_PROJECT_ID=pablohealth-oss  $0"
