#!/usr/bin/env bash
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
#
# Pablo on one Google Cloud VM, for a solo practice. Run in Cloud Shell:
#
#   ./install.sh
#
# It asks a few questions first (your email, the project, your own domain if
# you have one, Google sign-in, transcription), shows what it will create and
# what it costs, and runs to the end once you say yes. Every question has a
# flag, for a run with no questions at all.
#
# Sets up the project — billing, APIs, a service account for the VM, buckets,
# sign-in, secrets, the task queues and the HIPAA audit log sink — then
# creates the VM, whose startup script (deploy/vm/bootstrap.sh) installs the
# rest: Docker, Postgres with backups, Caddy for HTTPS, the scheduled jobs.
# Safe to run again: it picks up where it stopped, and an existing VM is
# updated and restarted rather than replaced. Its data disk is never deleted.
#
# Options:
#   --project ID        Google Cloud project, new or existing (default: a new one)
#   --admin-email EMAIL The clinician who signs in first
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
#   --google-signin     Turn on Google sign-in and calendar (one console step)
#   --skip-google-signin  Email and password sign-in only (the default)
#   --no-transcription  Leave transcription off; no AssemblyAI key needed
#   --no-lock-audit-retention
#                       Leave the audit log bucket's 6-year retention unlocked.
#                       Only for an evaluation project you will throw away:
#                       locked is what HIPAA asks for, and the lock is permanent.
#   --yes               Skip the final confirmation
#   --target local      Run on this machine (Docker Desktop and Tailscale
#                       Funnel) instead of a VM. For development and demos,
#                       never for real records.
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
# shellcheck source=deploy/vm/lib/env.sh
source "${DEPLOY_DIR}/lib/env.sh"
# shellcheck source=deploy/vm/lib/local.sh
source "${DEPLOY_DIR}/lib/local.sh"

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'
step() { echo -e "\n${BOLD}$*${NC}"; }
done_() { echo -e "${GREEN}$*${NC}"; }
warn() { echo -e "${YELLOW}$*${NC}"; }

usage() { sed -n '3,/^$/p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

PROJECT_ID=""
ADMIN_EMAIL=""
CUSTOM_HOSTNAME=""
REGION=us-central1
ZONE=""
MACHINE_TYPE=e2-small
DATA_DISK_GB=20
PABLO_VERSION=""
DEPLOY_REF=""
BAA_ACCEPTED=false
GOOGLE_SIGNIN=""
NO_TRANSCRIPTION=false
LOCK_AUDIT_RETENTION=true
ASSUME_YES=false
TARGET=vm

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
        --google-signin) GOOGLE_SIGNIN=true; shift ;;
        --skip-google-signin) GOOGLE_SIGNIN=false; shift ;;
        --yes) ASSUME_YES=true; shift ;;
        --target) TARGET="$2"; shift 2 ;;
        --no-transcription) NO_TRANSCRIPTION=true; shift ;;
        --no-lock-audit-retention) LOCK_AUDIT_RETENTION=false; shift ;;
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

if [ -z "$PABLO_VERSION" ]; then
    PABLO_VERSION=$(git ls-remote --tags --refs --sort=-v:refname "$REPO_URL" 'v*' | head -1 | sed 's|.*refs/tags/||')
fi
[ -n "$PABLO_VERSION" ] || { echo "Could not find a release; pass --version." >&2; exit 1; }

# --- Questions, all before anything is created -------------------------------
echo -e "${BOLD}Pablo — install on your own Google Cloud${NC}"
echo

if [ -z "$ADMIN_EMAIL" ]; then
    read -rp "Your email (you sign in with it): " ADMIN_EMAIL
fi
ADMIN_EMAIL="$(tr '[:upper:]' '[:lower:]' <<<"$ADMIN_EMAIL")"
[[ "$ADMIN_EMAIL" =~ ^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$ ]] || { echo "Not an email address: ${ADMIN_EMAIL}" >&2; exit 1; }

# Never gcloud's current project by default: on an admin's machine that can
# be anything, production included. Enter makes a new one.
if [ -z "$PROJECT_ID" ]; then
    suggested="pablo-$(openssl rand -hex 3)"
    read -rp "Google Cloud project [new: ${suggested}]: " PROJECT_ID
    PROJECT_ID="${PROJECT_ID:-$suggested}"
fi
if gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1 \
    && ! gcloud iam service-accounts describe "pablo-vm@${PROJECT_ID}.iam.gserviceaccount.com" --project="$PROJECT_ID" >/dev/null 2>&1 \
    && [ "$ASSUME_YES" != true ]; then
    warn "${PROJECT_ID} already exists and has no Pablo install in it."
    read -rp "Install Pablo into it anyway? [y/N] " answer
    [[ "$answer" =~ ^[Yy] ]] || { echo "Run this again and press Enter for a new project." >&2; exit 1; }
fi

[[ "$TARGET" == vm || "$TARGET" == local ]] || { echo "--target is vm or local" >&2; exit 2; }
if [ "$TARGET" = local ]; then
    command -v docker >/dev/null || { echo "Needs Docker Desktop." >&2; exit 1; }
    local_address
fi

if [ -z "$CUSTOM_HOSTNAME" ] && [ "$ASSUME_YES" != true ] && [ "$TARGET" = vm ]; then
    read -rp "Your own domain for Pablo, if you have one [Enter for an automatic address]: " CUSTOM_HOSTNAME
fi

if [ -z "$GOOGLE_SIGNIN" ]; then
    GOOGLE_SIGNIN=false
    if [ "$ASSUME_YES" != true ]; then
        read -rp "Also sign in with Google and sync Google Calendar? Needs one step in the console. [y/N] " answer
        [[ "$answer" =~ ^[Yy] ]] && GOOGLE_SIGNIN=true
    fi
fi

ask_transcription

if [ "$BAA_ACCEPTED" != true ]; then
    cat <<EOF

Your records are covered by Google's Business Associate Agreement once an
administrator of your Google Cloud account accepts it, one time:
  https://cloud.google.com/security/compliance/hipaa
EOF
    read -rp "Is it accepted? [y/N] " baa
    [[ "$baa" =~ ^[Yy] ]] || { echo "Run this again once it is accepted." >&2; exit 1; }
fi

echo
echo -e "${BOLD}Ready to install${NC}"
echo "  Project   ${PROJECT_ID}"
if [ "$TARGET" = local ]; then
    echo "  Address   ${PUBLIC_ORIGIN} (Tailscale Funnel)"
else
    echo "  Address   ${CUSTOM_HOSTNAME:-an automatic *.sslip.io address}"
fi
echo "  Sign-in   ${ADMIN_EMAIL}, email and password$([ "$GOOGLE_SIGNIN" = true ] && echo ", and Google")"
if [ "$TARGET" = local ]; then
    echo "  Server    this machine, in Docker. For development and demos, not real records."
else
    echo "  Server    ${MACHINE_TYPE} in ${ZONE}, ${DATA_DISK_GB} GB for records, backed up daily"
fi
echo "  Release   ${PABLO_VERSION}${DEPLOY_REF:+ (deploy files at ${DEPLOY_REF})}"
if [ "$LOCK_AUDIT_RETENTION" = true ]; then
    echo "  Audit log kept 6 years in a locked bucket: it cannot be shortened or deleted"
else
    warn "  Audit log kept 6 years, not locked (--no-lock-audit-retention): not for real records"
fi
if [ "$TARGET" = vm ]; then
    echo "  Cost      about \$20 a month to Google, plus AI and transcription by use"
fi
if [ "$ASSUME_YES" != true ]; then
    read -rp "Continue? [Y/n] " answer
    [[ -z "$answer" || "$answer" =~ ^[Yy] ]] || exit 1
fi

# From here on a failure is resumable; say how, so a second run does not make
# a second project.
config_file=""
on_exit() {
    local status=$?
    [ -z "$config_file" ] || rm -f "$config_file"
    local resume="./install.sh --project ${PROJECT_ID}"
    [ "$TARGET" = vm ] || resume="${resume} --target ${TARGET}"
    [ "$status" -eq 0 ] || warn "\nStopped. To pick up where this left off:  ${resume}" >&2
}
trap on_exit EXIT

# --- 1. Project ----------------------------------------------------------------
step "1/7  Project and billing"
ensure_project
ensure_billing
enable_apis
done_ "Project ready"

# --- 2. Address and hostname ---------------------------------------------------
step "2/7  Address and hostname"
if [ "$TARGET" = local ]; then
    done_ "${PUBLIC_ORIGIN}"
else
ensure_static_ip
if [ -n "$CUSTOM_HOSTNAME" ]; then
    PABLO_HOST="$CUSTOM_HOSTNAME"
    warn "Point ${PABLO_HOST} at ${VM_IP} with an A record, if you have not yet."
    warn "The certificate is issued as soon as that record resolves."
else
    PABLO_HOST="${VM_IP//./-}.sslip.io"
fi
done_ "https://${PABLO_HOST} → ${VM_IP}"
fi

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
# The sink script locks the bucket's retention after verifying it; locking is
# permanent, which is why the summary above said so before anything was made.
audit_sink_lock=""
[ "$LOCK_AUDIT_RETENTION" = true ] || audit_sink_lock=--no-lock
"${REPO_ROOT}/scripts/setup-hipaa-audit-sink.sh" "$PROJECT_ID" ${audit_sink_lock:+"$audit_sink_lock"}
GCP_PROJECT_ID="$PROJECT_ID" GCP_REGION="$REGION" BACKEND_SA="$VM_SA" \
    "${REPO_ROOT}/scripts/setup-transcription-queue.sh"
done_ "Audit sink and queues ready"

# --- 7. The machine -------------------------------------------------------------
if [ "$TARGET" = local ]; then
    step "7/7  This machine"
    PABLO_LOCAL_DIR="${HOME}/.pablo/${PROJECT_ID}"
    mkdir -p "$PABLO_LOCAL_DIR"
    chmod 700 "$PABLO_LOCAL_DIR"
    check_disk_encryption
    ensure_local_credentials
    start_local
    deadline=$(($(date +%s) + 600))
    until curl -fsS -o /dev/null "${PUBLIC_ORIGIN}/api/health" 2>/dev/null; do
        if [ "$(date +%s)" -ge "$deadline" ]; then
            warn "Not answering at ${PUBLIC_ORIGIN} after 10 minutes. Check: docker compose -f deploy/workstation/compose.yaml ps"
            exit 1
        fi
        sleep 10
    done
    install_local_backups
    echo
    done_ "Pablo is running on this machine: ${PUBLIC_ORIGIN}"
    echo "Sign in with ${ADMIN_EMAIL}. Notes and transcription finish only while this machine is awake."
    echo "Backups:  nightly to gs://${BACKUP_BUCKET}/workstation/, kept 30 days"
    echo "          check a restore with ${PABLO_LOCAL_DIR}/bin/pablo-local restore-drill"
    echo "Stop it:  PABLO_LOCAL_DIR=${PABLO_LOCAL_DIR} PABLO_VERSION=${PABLO_VERSION} docker compose -f deploy/workstation/compose.yaml down"
    exit 0
fi

step "7/7  The VM"
ensure_firewall
ensure_data_disk

config_file="$(mktemp)"
RUN_ID="$(date +%s)-$(openssl rand -hex 3)"
# Non-secret settings only; the VM reads secrets from Secret Manager itself.
cat > "$config_file" <<EOF
RUN_ID=${RUN_ID}
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
# Ready means the VM says so for this run: the site, the first clinician
# admitted and the first backup taken, not just a health check answering.
deadline=$(($(date +%s) + 1800))
while :; do
    state=$(gcloud compute instances get-guest-attributes "$VM_NAME" --zone="$ZONE" \
        --query-path=pablo/state --format='value(value)' 2>/dev/null || true)
    case "$state" in
        "ready ${RUN_ID}") break ;;
        "failed at: "*" ${RUN_ID}")
            warn "The VM stopped while ${state% "${RUN_ID}"}."
            warn "Its log: gcloud compute instances get-serial-port-output ${VM_NAME} --zone=${ZONE} | grep pablo-"
            exit 1
            ;;
    esac
    if [ "$(date +%s)" -ge "$deadline" ]; then
        warn "Not ready after 30 minutes. Check the log above, then run this again."
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
