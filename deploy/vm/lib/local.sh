# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
# shellcheck shell=bash
#
# install.sh --target local: the engine on this machine instead of a VM, for
# development and demos. Sourced, not run. Expects the values install.sh has
# set by its last step (project, buckets, sign-in, VM_SA, PABLO_VERSION).
#
# Cloud Tasks calls back into the backend for transcription, notes and
# calendar sync, so the machine needs a public HTTPS address: Tailscale Funnel
# gives it one at https://<machine>.<tailnet>.ts.net, on port 8443 so it can
# sit beside anything already served on 443.

readonly FUNNEL_PORT=8443
readonly LOCAL_HTTP_PORT=8080

tailscale_bin() {
    command -v tailscale || echo /Applications/Tailscale.app/Contents/MacOS/Tailscale
}

# Sets PABLO_HOST and PUBLIC_ORIGIN from this machine's Tailscale name.
local_address() {
    local ts
    ts="$(tailscale_bin)"
    [ -x "$ts" ] || { echo "Needs Tailscale: https://tailscale.com/download" >&2; exit 1; }
    PABLO_HOST="$("$ts" status --json | python3 -c 'import sys, json; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
    [ -n "$PABLO_HOST" ] || { echo "Tailscale is not signed in on this machine." >&2; exit 1; }
    PUBLIC_ORIGIN="https://${PABLO_HOST}:${FUNNEL_PORT}"
}

# Google credentials for the containers without a key file (organisations
# commonly forbid them): your own application-default login, impersonating
# the VM's service account. Google trades it for short-lived tokens as that
# account; revoking either side ends it. Written beside the env files, so the
# global gcloud configuration is untouched.
ensure_local_credentials() {
    local adc="${CLOUDSDK_CONFIG:-$HOME/.config/gcloud}/application_default_credentials.json"
    local token account
    if ! token="$(gcloud auth application-default print-access-token 2>/dev/null)"; then
        echo "Sign in for local credentials first:  gcloud auth application-default login" >&2
        exit 1
    fi
    account="$(curl -fsS "https://oauth2.googleapis.com/tokeninfo?access_token=${token}" \
        | python3 -c 'import sys, json; print(json.load(sys.stdin)["email"])')"
    gcloud iam service-accounts add-iam-policy-binding "$VM_SA" \
        --member="user:${account}" --role=roles/iam.serviceAccountTokenCreator >/dev/null
    (umask 077 && python3 - "$adc" "$VM_SA" > "${PABLO_LOCAL_DIR}/google.json" <<'PY'
import json, sys
source = json.load(open(sys.argv[1]))
print(json.dumps({
    "type": "impersonated_service_account",
    "service_account_impersonation_url":
        f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{sys.argv[2]}:generateAccessToken",
    "source_credentials": source,
    "delegates": [],
}))
PY
    )
}

# Expose loopback HTTP publicly over HTTPS. Needs Funnel allowed for this
# machine in the tailnet policy, which only a tailnet admin can grant.
ensure_funnel() {
    local ts
    ts="$(tailscale_bin)"
    if ! "$ts" funnel --bg --https="$FUNNEL_PORT" "http://127.0.0.1:${LOCAL_HTTP_PORT}" >/dev/null 2>&1; then
        cat >&2 <<EOF
Tailscale Funnel is not allowed for this machine yet. In the Tailscale admin
console, add the "funnel" attribute for it in the access policy:
  https://tailscale.com/kb/1223/funnel
then run this again.
EOF
        exit 1
    fi
}

# The machine holds the records, so its disk should be encrypted. Checked
# where the OS lets an ordinary user ask (FileVault); a warning, not a stop.
check_disk_encryption() {
    if command -v fdesetup >/dev/null; then
        fdesetup status 2>/dev/null | grep -q "FileVault is On" \
            || warn "FileVault is off. Turn it on (System Settings > Privacy & Security) before real records live here."
    else
        warn "Make sure this machine's disk is encrypted (BitLocker on Windows) before real records live here."
    fi
}

# Nightly database dumps to the backup bucket, kept 30 days, plus the
# command to run them by hand and to check a restore (pablo-local).
install_local_backups() {
    install -d -m 0700 "${PABLO_LOCAL_DIR}/bin"
    install -m 0755 "${DEPLOY_DIR}/../workstation/bin/pablo-local" "${PABLO_LOCAL_DIR}/bin/pablo-local"
    (umask 077 && cat > "${PABLO_LOCAL_DIR}/local.env" <<EOF
PROJECT_ID=${PROJECT_ID}
BACKUP_BUCKET=${BACKUP_BUCKET}
PABLO_VERSION=${PABLO_VERSION}
COMPOSE_FILE=$(cd "${DEPLOY_DIR}/../workstation" && pwd)/compose.yaml
EOF
    )

    local lifecycle
    lifecycle="$(mktemp)"
    printf '%s' '{"rule": [{"action": {"type": "Delete"}, "condition": {"age": 30, "matchesPrefix": ["workstation/"]}}]}' > "$lifecycle"
    gcloud storage buckets update "gs://${BACKUP_BUCKET}" --lifecycle-file="$lifecycle" >/dev/null
    rm -f "$lifecycle"

    local command="${PABLO_LOCAL_DIR}/bin/pablo-local backup"
    if [ "$(uname)" = Darwin ]; then
        # launchd runs a missed time once the Mac wakes, unlike cron.
        local plist="${HOME}/Library/LaunchAgents/health.pablo.backup.${PROJECT_ID}.plist"
        mkdir -p "$(dirname "$plist")"
        cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>health.pablo.backup.${PROJECT_ID}</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>-lc</string><string>${command}</string>
  </array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>2</integer><key>Minute</key><integer>30</integer></dict>
  <key>StandardOutPath</key><string>${PABLO_LOCAL_DIR}/backup.log</string>
  <key>StandardErrorPath</key><string>${PABLO_LOCAL_DIR}/backup.log</string>
</dict></plist>
EOF
        launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
        launchctl bootstrap "gui/$(id -u)" "$plist"
    else
        local line="30 2 * * * /bin/bash -lc '${command}' >> ${PABLO_LOCAL_DIR}/backup.log 2>&1"
        { crontab -l 2>/dev/null | grep -vF "${PABLO_LOCAL_DIR}/bin/pablo-local" || true; echo "$line"; } | crontab -
    fi
    "${PABLO_LOCAL_DIR}/bin/pablo-local" backup
}

start_local() {
    local compose=(docker compose -f "${DEPLOY_DIR}/../workstation/compose.yaml")
    export PABLO_LOCAL_DIR PABLO_VERSION
    # shellcheck disable=SC2034  # read by render_env_files
    VERSION="$PABLO_VERSION"
    render_env_files "$PABLO_LOCAL_DIR" "$PUBLIC_ORIGIN" postgres "http://caddy"
    # The impersonated credentials sign custom tokens (passkeys) through IAM
    # only when firebase-admin is told which account to sign as.
    echo "FIREBASE_SERVICE_ACCOUNT_ID=${VM_SA}" >> "${PABLO_LOCAL_DIR}/backend.env"
    (umask 077 && printf 'POSTGRES_PASSWORD=%s\n' "$(secret pablo-db-password)" > "${PABLO_LOCAL_DIR}/postgres.env")

    "${compose[@]}" --profile tools pull --quiet
    "${compose[@]}" run --rm tool backend/bin/migrate.py
    "${compose[@]}" run --rm tool backend/bin/migrate_tenants.py
    "${compose[@]}" run --rm \
        -v "${REPO_ROOT}/backend/bin/allow_email.py:/app/backend/bin/allow_email.py:ro" \
        tool backend/bin/allow_email.py "$ADMIN_EMAIL"
    "${compose[@]}" up -d --wait --wait-timeout 600
    ensure_funnel
}
