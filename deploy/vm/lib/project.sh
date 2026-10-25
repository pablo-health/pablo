# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
# shellcheck shell=bash
#
# The Google Cloud project for install.sh: billing, APIs, the VM's service
# account and its buckets. Sourced, not run. Every function is safe to repeat.
# Expects PROJECT_ID and REGION.

readonly APIS=(
    compute.googleapis.com
    iam.googleapis.com
    iamcredentials.googleapis.com
    cloudresourcemanager.googleapis.com
    serviceusage.googleapis.com
    secretmanager.googleapis.com
    logging.googleapis.com
    monitoring.googleapis.com
    storage.googleapis.com
    cloudtasks.googleapis.com
    aiplatform.googleapis.com
    identitytoolkit.googleapis.com
    firebase.googleapis.com
    cloudbilling.googleapis.com
)

# What the backend, frontend and jobs need, all now running as one identity.
# Bucket access is granted per bucket in ensure_buckets, not project-wide.
readonly VM_ROLES=(
    roles/aiplatform.user
    roles/identitytoolkit.admin
    roles/logging.logWriter
    roles/monitoring.metricWriter
    roles/secretmanager.secretAccessor
    roles/cloudtasks.enqueuer
)

ensure_project() {
    if ! gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1; then
        echo "Creating project ${PROJECT_ID}"
        gcloud projects create "$PROJECT_ID" --name="Pablo"
    fi
    # For this run only; the caller's gcloud configuration is left alone.
    export CLOUDSDK_CORE_PROJECT="$PROJECT_ID"
}

ensure_billing() {
    if [ "$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingEnabled)')" = "True" ]; then
        return
    fi
    local accounts=() account
    while read -r account; do
        [ -n "$account" ] && accounts+=("$account")
    done < <(gcloud billing accounts list --filter=open=true --format='value(name)')
    case ${#accounts[@]} in
        0)
            echo "No billing account found. Add one, then run this again:" >&2
            echo "  https://console.cloud.google.com/billing/create" >&2
            exit 1
            ;;
        1) BILLING_ACCOUNT="${accounts[0]}" ;;
        *)
            gcloud billing accounts list --filter=open=true
            read -rp "Billing account ID to use: " BILLING_ACCOUNT
            ;;
    esac
    gcloud billing projects link "$PROJECT_ID" --billing-account="${BILLING_ACCOUNT#billingAccounts/}" >/dev/null
}

enable_apis() {
    gcloud services enable "${APIS[@]}" --project="$PROJECT_ID"
    # On a new project "enabled" comes back before Compute Engine serves
    # requests; the next step would fail with SERVICE_DISABLED.
    local tries=36
    until gcloud compute regions describe "$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; do
        tries=$((tries - 1))
        [ "$tries" -gt 0 ] || { echo "Compute Engine did not come up; run this again in a few minutes." >&2; exit 1; }
        sleep 5
    done
}

# Sets VM_SA.
ensure_vm_service_account() {
    VM_SA="pablo-vm@${PROJECT_ID}.iam.gserviceaccount.com"
    if ! gcloud iam service-accounts describe "$VM_SA" >/dev/null 2>&1; then
        gcloud iam service-accounts create pablo-vm \
            --display-name="Pablo VM" \
            --description="Runs the Pablo VM: backend, frontend, jobs, backups. Do not reuse." >/dev/null
        # A new account takes a moment to be visible to IAM policy writes.
        sleep 15
    fi
    local role
    for role in "${VM_ROLES[@]}"; do
        gcloud projects add-iam-policy-binding "$PROJECT_ID" \
            --member="serviceAccount:${VM_SA}" --role="$role" --condition=None >/dev/null
    done
    # Signing as itself, and only itself: the frontend signs session cookies
    # and the backend signs storage URLs with signBlob.
    gcloud iam service-accounts add-iam-policy-binding "$VM_SA" \
        --member="serviceAccount:${VM_SA}" --role=roles/iam.serviceAccountTokenCreator >/dev/null
}

# Sets BACKUP_BUCKET, AUDIO_BUCKET, DOCUMENTS_BUCKET, COMPLIANCE_BUCKET.
ensure_buckets() {
    BACKUP_BUCKET="${PROJECT_ID}-pablo-backups"
    AUDIO_BUCKET="${PROJECT_ID}-pablo-audio"
    DOCUMENTS_BUCKET="${PROJECT_ID}-pablo-documents"
    COMPLIANCE_BUCKET="${PROJECT_ID}-pablo-compliance"
    local bucket
    for bucket in "$BACKUP_BUCKET" "$AUDIO_BUCKET" "$DOCUMENTS_BUCKET" "$COMPLIANCE_BUCKET"; do
        if ! gcloud storage buckets describe "gs://${bucket}" >/dev/null 2>&1; then
            gcloud storage buckets create "gs://${bucket}" --location="$REGION" \
                --uniform-bucket-level-access --public-access-prevention >/dev/null
        fi
        gcloud storage buckets add-iam-policy-binding "gs://${bucket}" \
            --member="serviceAccount:${VM_SA}" --role=roles/storage.objectAdmin >/dev/null
    done
    # Compliance reports are evidence: kept seven years, not lockable by
    # default so a mistake can still be corrected.
    gcloud storage buckets update "gs://${COMPLIANCE_BUCKET}" --retention-period=7y >/dev/null
}
