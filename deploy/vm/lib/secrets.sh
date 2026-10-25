# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
# shellcheck shell=bash disable=SC2034  # sets globals that install.sh reads
#
# Secret Manager for install.sh. Sourced, not run. Expects PROJECT_ID.
# Generated secrets are created once and never rotated by a re-run; the VM
# reads them with pablo-update.

secret_exists() { gcloud secrets describe "$1" >/dev/null 2>&1; }

read_secret() { gcloud secrets versions access latest --secret="$1"; }

# put_secret <name> — value on stdin; adds a version if the secret exists.
put_secret() {
    if secret_exists "$1"; then
        gcloud secrets versions add "$1" --data-file=- >/dev/null
    else
        gcloud secrets create "$1" --replication-policy=automatic --data-file=- >/dev/null
    fi
}

# ensure_generated_secret <name> <command that prints a value>
ensure_generated_secret() {
    secret_exists "$1" || "${@:2}" | tr -d '\n' | put_secret "$1"
}

_hex32() { openssl rand -hex 32; }
# A Fernet key: 32 random bytes, URL-safe base64.
_fernet_key() { openssl rand -base64 32 | tr '+/' '-_'; }

ensure_generated_secrets() {
    ensure_generated_secret pablo-db-password _hex32
    ensure_generated_secret JWT_SECRET_KEY _hex32
    ensure_generated_secret AUTH_SECRET _hex32
    ensure_generated_secret AUTH_COOKIE_SIGNATURE_KEY _hex32
    ensure_generated_secret GOOGLE_CALENDAR_ENCRYPTION_KEY _fernet_key
}

# Sets GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET, asking only the first time.
# The OAuth client has no API, so it is made by hand in the console. Both
# stay empty when sign-in is email and password only (SKIP_GOOGLE_SIGNIN).
ensure_oauth_client() {
    GOOGLE_CLIENT_ID=""
    GOOGLE_CLIENT_SECRET=""
    if secret_exists GOOGLE_CLIENT_SECRET && secret_exists GOOGLE_CLIENT_ID; then
        GOOGLE_CLIENT_ID=$(read_secret GOOGLE_CLIENT_ID)
        GOOGLE_CLIENT_SECRET=$(read_secret GOOGLE_CLIENT_SECRET)
        return
    fi
    [ "${SKIP_GOOGLE_SIGNIN:-false}" = true ] && return
    cat <<EOF

Google sign-in needs an OAuth client, which Google only lets you make in the console:

  1. Open https://console.cloud.google.com/auth/overview?project=${PROJECT_ID}
     and set up the consent screen (app name "Pablo", your email, Internal or External).
  2. Open https://console.cloud.google.com/apis/credentials?project=${PROJECT_ID}
     Create credentials → OAuth client ID → Web application, named "Pablo".
  3. Authorized JavaScript origins:
       https://${PABLO_HOST}
     Authorized redirect URIs:
       https://${PABLO_HOST}/__/auth/handler
       https://${PROJECT_ID}.firebaseapp.com/__/auth/handler
  4. Create, then paste the two values here.

EOF
    read -rp "Client ID: " GOOGLE_CLIENT_ID
    read -rsp "Client secret (hidden): " GOOGLE_CLIENT_SECRET
    echo
    [ -n "$GOOGLE_CLIENT_ID" ] && [ -n "$GOOGLE_CLIENT_SECRET" ] || { echo "Both values are needed." >&2; exit 1; }
    printf '%s' "$GOOGLE_CLIENT_ID" | put_secret GOOGLE_CLIENT_ID
    printf '%s' "$GOOGLE_CLIENT_SECRET" | put_secret GOOGLE_CLIENT_SECRET
}

# Sets TRANSCRIPTION_PROVIDER and TRANSCRIPTION_ENABLED. Session audio is
# PHI: AssemblyAI is used only with the clinician's own BAA with AssemblyAI,
# stated explicitly. NO_TRANSCRIPTION=true leaves transcription off.
ensure_transcription() {
    TRANSCRIPTION_PROVIDER=assemblyai
    TRANSCRIPTION_ENABLED=true
    secret_exists pablo-assemblyai-api-key && return
    if [ "${NO_TRANSCRIPTION:-false}" = true ]; then
        TRANSCRIPTION_ENABLED=false
        return
    fi
    cat <<EOF

Transcription runs on AssemblyAI, which needs its own signed BAA with you
before any session audio is sent. The free tier does not include one:
  https://www.assemblyai.com/legal/business-associate-agreement

EOF
    local ack key
    read -rp "Type 'I have a signed AssemblyAI BAA' to continue: " ack
    if [ "$ack" != "I have a signed AssemblyAI BAA" ]; then
        echo "Stopping here. Run this again once the AssemblyAI BAA is signed." >&2
        exit 1
    fi
    read -rsp "AssemblyAI API key (hidden): " key
    echo
    [ -n "$key" ] || { echo "The key is needed." >&2; exit 1; }
    printf '%s' "$key" | put_secret pablo-assemblyai-api-key
}
