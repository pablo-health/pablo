#!/usr/bin/env bash
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
#
# Pablo on one Google Cloud VM, for a solo practice. Run in Cloud Shell:
#
#   ./install.sh --admin-email you@example.com [--hostname notes.example.com]
#
# Sets up the project — billing, APIs, a service account for the VM, buckets,
# sign-in, secrets, the task queues and the HIPAA audit log sink — then
# creates the VM, whose startup script (deploy/vm/bootstrap.sh) installs the
# rest: Docker, Postgres with backups, Caddy for HTTPS, the scheduled jobs.
# Safe to run again: it picks up where it stopped, and an existing VM is
# updated and restarted rather than replaced. Its data disk is never deleted.
#
# Options:
#   --project ID        Google Cloud project (default: gcloud's current one)
#   --admin-email EMAIL The clinician who signs in first (required)
#   --hostname NAME     Your own domain, pointed at the VM's address with an A
#                       record. Default: an sslip.io name for that address.
#   --region REGION     Default us-central1
#   --zone ZONE         Default <region>-a
#   --machine-type T    Default e2-small (2 GB, about $12/month)
#   --disk-gb N         Database disk size, default 20
#   --version vX.Y.Z    Release to install (default: the latest)
#   --deploy-ref REF    Git ref for the VM's deploy files, when testing a
#                       branch before it is released (default: --version)
#   --baa-accepted      Skip the BAA question (unattended runs)
#   --skip-google-signin  Email and password sign-in only; no OAuth client
#   --no-transcription  Leave transcription off; no AssemblyAI key needed
#
# Before running: accept the Google Cloud BAA for the account that owns this
# project. Nothing here can do that for you, and no patient data belongs on
# the VM until it is in place.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT
readonly DEPLOY_DIR="${REPO_ROOT}/deploy/vm"
readonly REPO_URL="https://github.com/pablo-health/pablo.git"

# shellcheck source=deploy/vm/lib/project.sh
source "${DEPLOY_DIR}/lib/project.sh"
# shellcheck source=deploy/vm/lib/identity.sh
source "${DEPLOY_DIR}/lib/identity.sh"
# shellcheck source=deploy/vm/lib/secrets.sh
source "${DEPLOY_DIR}/lib/secrets.sh"
# shellcheck source=deploy/vm/lib/vm.sh
source "${DEPLOY_DIR}/lib/vm.sh"

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'
step() { echo -e "\n${BOLD}$*${NC}"; }
done_() { echo -e "${GREEN}$*${NC}"; }
warn() { echo -e "${YELLOW}$*${NC}"; }

usage() { sed -n '3,/^$/p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
ADMIN_EMAIL=""
CUSTOM_HOSTNAME=""
REGION=us-central1
ZONE=""
MACHINE_TYPE=e2-small
DATA_DISK_GB=20
PABLO_VERSION=""
DEPLOY_REF=""
BAA_ACCEPTED=false
SKIP_GOOGLE_SIGNIN=false
NO_TRANSCRIPTION=false

while [ $# -gt 0 ]; do
    case "$1" in
        --project) PROJECT_ID="$2"; shift 2 ;;
        --admin-email) ADMIN_EMAIL="$2"; shift 2 ;;
        --hostname) CUSTOM_HOSTNAME="$2"; shift 2 ;;
        --region) REGION="$2"; shift 2 ;;
        --zone) ZONE="$2"; shift 2 ;;
        --machine-type) MACHINE_TYPE="$2"; shift 2 ;;
        --disk-gb) DATA_DISK_GB="$2"; shift 2 ;;
        --version) PABLO_VERSION="$2"; shift 2 ;;
        --deploy-ref) DEPLOY_REF="$2"; shift 2 ;;
        --baa-accepted) BAA_ACCEPTED=true; shift ;;
        --skip-google-signin) SKIP_GOOGLE_SIGNIN=true; shift ;;
        --no-transcription) NO_TRANSCRIPTION=true; shift ;;
        -h | --help) usage ;;
        *) echo "Unknown option: $1" >&2; usage 2 ;;
    esac
done
ZONE="${ZONE:-${REGION}-a}"

# --- Preflight ---------------------------------------------------------------
for tool in gcloud curl python3 openssl git; do
    command -v "$tool" >/dev/null || { echo "Needs ${tool}; Cloud Shell has all of these." >&2; exit 1; }
done
gcloud auth print-access-token >/dev/null 2>&1 || { echo "Sign in first: gcloud auth login" >&2; exit 1; }

if [ -z "$ADMIN_EMAIL" ]; then
    read -rp "Email you will sign in with: " ADMIN_EMAIL
fi
ADMIN_EMAIL="$(tr '[:upper:]' '[:lower:]' <<<"$ADMIN_EMAIL")"
[[ "$ADMIN_EMAIL" =~ ^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$ ]] || { echo "Not an email address: ${ADMIN_EMAIL}" >&2; exit 1; }

if [ -z "$PROJECT_ID" ]; then
    read -rp "Google Cloud project ID (new or existing): " PROJECT_ID
fi

if [ -z "$PABLO_VERSION" ]; then
    PABLO_VERSION=$(git ls-remote --tags --refs --sort=-v:refname "$REPO_URL" 'v*' | head -1 | sed 's|.*refs/tags/||')
fi
[ -n "$PABLO_VERSION" ] || { echo "Could not find a release; pass --version." >&2; exit 1; }

echo -e "${BOLD}Pablo — single-VM install${NC}"
echo "  Project  ${PROJECT_ID}"
echo "  Release  ${PABLO_VERSION}${DEPLOY_REF:+ (deploy files at ${DEPLOY_REF})}"
echo "  VM       ${MACHINE_TYPE} in ${ZONE}, ${DATA_DISK_GB} GB database disk"

# --- 1. Project ----------------------------------------------------------------
step "1/7  Project and billing"
ensure_project
ensure_billing
if [ "$BAA_ACCEPTED" != true ]; then
    cat <<EOF

Pablo stores clinical records, so this project must be covered by the Google
Cloud Business Associate Agreement. An administrator of the account accepts
it once in the console:
  https://cloud.google.com/security/compliance/hipaa

EOF
    read -rp "Is the Google Cloud BAA accepted for this account? [y/N] " baa
    [[ "$baa" =~ ^[Yy] ]] || { echo "Run this again once the BAA is accepted." >&2; exit 1; }
fi
enable_apis
done_ "Project ready"

# --- 2. Address and hostname ---------------------------------------------------
step "2/7  Address and hostname"
ensure_static_ip
if [ -n "$CUSTOM_HOSTNAME" ]; then
    PABLO_HOST="$CUSTOM_HOSTNAME"
    warn "Point ${PABLO_HOST} at ${VM_IP} with an A record, if you have not yet."
    warn "The certificate is issued as soon as that record resolves."
else
    PABLO_HOST="${VM_IP//./-}.sslip.io"
fi
done_ "https://${PABLO_HOST} → ${VM_IP}"

# --- 3. Identity and storage ---------------------------------------------------
step "3/7  Service account and buckets"
ensure_vm_service_account
ensure_buckets
done_ "VM runs as ${VM_SA}"

# --- 4. Sign-in ----------------------------------------------------------------
step "4/7  Sign-in (Identity Platform)"
ensure_firebase
ensure_web_app
configure_identity_platform
add_authorized_domain "$PABLO_HOST"
done_ "Email and Google sign-in, 15-character passwords, authenticator-app MFA"

# --- 5. Secrets ----------------------------------------------------------------
step "5/7  Secrets"
ensure_generated_secrets
ensure_oauth_client
if [ -n "$GOOGLE_CLIENT_ID" ]; then
    register_google_idp "$GOOGLE_CLIENT_ID" "$GOOGLE_CLIENT_SECRET"
fi
unset GOOGLE_CLIENT_SECRET
ensure_transcription
done_ "Secrets in Secret Manager"

# --- 6. Audit log and queues ---------------------------------------------------
step "6/7  Audit log retention and task queues"
"${REPO_ROOT}/scripts/setup-hipaa-audit-sink.sh" "$PROJECT_ID"
GCP_PROJECT_ID="$PROJECT_ID" GCP_REGION="$REGION" BACKEND_SA="$VM_SA" \
    "${REPO_ROOT}/scripts/setup-transcription-queue.sh"
done_ "Audit sink and queues ready"

# --- 7. The VM -----------------------------------------------------------------
step "7/7  The VM"
ensure_firewall
ensure_data_disk

config_file="$(mktemp)"
trap 'rm -f "$config_file"' EXIT
# Non-secret settings only; the VM reads secrets from Secret Manager itself.
cat > "$config_file" <<EOF
PROJECT_ID=${PROJECT_ID}
REGION=${REGION}
PABLO_HOST=${PABLO_HOST}
PABLO_VERSION=${PABLO_VERSION}
DEPLOY_REF=${DEPLOY_REF:-$PABLO_VERSION}
REPO_URL=${REPO_URL}
ADMIN_EMAIL=${ADMIN_EMAIL}
BACKUP_BUCKET=${BACKUP_BUCKET}
AUDIO_BUCKET=${AUDIO_BUCKET}
DOCUMENTS_BUCKET=${DOCUMENTS_BUCKET}
COMPLIANCE_BUCKET=${COMPLIANCE_BUCKET}
TRANSCRIPTION_PROVIDER=${TRANSCRIPTION_PROVIDER}
TRANSCRIPTION_ENABLED=${TRANSCRIPTION_ENABLED}
GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}
FIREBASE_API_KEY=${FIREBASE_API_KEY}
FIREBASE_AUTH_DOMAIN=${FIREBASE_AUTH_DOMAIN}
FIREBASE_APP_ID=${FIREBASE_APP_ID}
EOF
ensure_vm "$config_file"
done_ "VM ${VM_NAME} is starting"

echo
echo "The VM is installing Pablo. The first boot takes about ten minutes."
echo "Follow along with:"
echo "  gcloud compute instances get-serial-port-output ${VM_NAME} --zone=${ZONE} | grep pablo-"
echo
deadline=$(($(date +%s) + 1800))
until curl -fsS -o /dev/null "https://${PABLO_HOST}/api/health" 2>/dev/null; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
        warn "Not answering after 30 minutes. Check the log above, then run this again."
        exit 1
    fi
    sleep 20
done

echo
done_ "Pablo is running: https://${PABLO_HOST}"
echo "Sign in with ${ADMIN_EMAIL}. You will set up an authenticator app the first time."
echo
echo "Updates:  gcloud compute ssh ${VM_NAME} --zone=${ZONE} --tunnel-through-iap -- sudo pablo-update vX.Y.Z"
echo "Backups:  daily to gs://${BACKUP_BUCKET}, restore-tested monthly (sudo pablo-backup info)"
