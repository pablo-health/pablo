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
    # Lets the frontend server pass the end user's IP to the backend for
    # audit records. Both services read the same value.
    ensure_generated_secret FRONTEND_FORWARD_SECRET _hex32
    ensure_generated_secret GOOGLE_CALENDAR_ENCRYPTION_KEY _fernet_key
}

# Sets GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET, asking only the first time.
# The OAuth client has no API, so it is made by hand in the console. Both
# stay empty when sign-in is email and password only (GOOGLE_SIGNIN not true).
#
# The same client serves Google Calendar. It is the clinician's own, in their
# own project, so calendar data never passes through anything Pablo runs.
# Its calendar scopes are "sensitive", so an External app left unverified
# shows Google's one-time warning; "In production" matters, because a
# Testing app's refresh tokens expire after seven days.
ensure_oauth_client() {
    GOOGLE_CLIENT_ID=""
    GOOGLE_CLIENT_SECRET=""
    if secret_exists GOOGLE_CLIENT_SECRET && secret_exists GOOGLE_CLIENT_ID; then
        GOOGLE_CLIENT_ID=$(read_secret GOOGLE_CLIENT_ID)
        GOOGLE_CLIENT_SECRET=$(read_secret GOOGLE_CLIENT_SECRET)
        return
    fi
    [ "${GOOGLE_SIGNIN:-false}" = true ] || return 0
    cat <<EOF

Google sign-in and Google Calendar need an OAuth client, which Google only
lets you make in the console:

  1. Open https://console.cloud.google.com/auth/overview?project=${PROJECT_ID}
     Set up the consent screen: app name "Pablo", your email. Choose Internal
     if you use Google Workspace, otherwise External.
  2. Under Data access, add these scopes:
       .../auth/calendar.events  .../auth/calendar.readonly
       .../auth/calendar.freebusy  .../auth/calendar.app.created
  3. If you chose External: under Audience, select Publish app, so it is
     "In production". Google shows you a one-time "unverified app" screen
     when you connect your calendar; that is expected for your own app.
  4. Open https://console.cloud.google.com/apis/credentials?project=${PROJECT_ID}
     Create credentials → OAuth client ID → Web application, named "Pablo".
  5. Authorized JavaScript origins:
       ${PUBLIC_ORIGIN:-https://${PABLO_HOST}}
     Authorized redirect URIs:
       ${PUBLIC_ORIGIN:-https://${PABLO_HOST}}/__/auth/handler
       https://${PROJECT_ID}.firebaseapp.com/__/auth/handler
       ${PUBLIC_ORIGIN:-https://${PABLO_HOST}}/dashboard/settings/calendar
  6. Create, then paste the two values here (or press Enter twice to use
     email and password only, without Google sign-in or calendar sync).

EOF
    read -rp "Client ID: " GOOGLE_CLIENT_ID
    read -rsp "Client secret (hidden): " GOOGLE_CLIENT_SECRET
    echo
    if [ -z "$GOOGLE_CLIENT_ID" ] && [ -z "$GOOGLE_CLIENT_SECRET" ]; then
        echo "Email and password sign-in only. Run this again later to add Google."
        return
    fi
    [ -n "$GOOGLE_CLIENT_ID" ] && [ -n "$GOOGLE_CLIENT_SECRET" ] || { echo "Both values are needed." >&2; exit 1; }
    printf '%s' "$GOOGLE_CLIENT_ID" | put_secret GOOGLE_CLIENT_ID
    printf '%s' "$GOOGLE_CLIENT_SECRET" | put_secret GOOGLE_CLIENT_SECRET
}

# Asked before anything is created; sets ASSEMBLYAI_KEY (empty: off).
# Session audio is PHI, so the question names the AssemblyAI BAA; a yes is
# the clinician's statement that it is signed. A key already stored in the
# project (a re-run) is kept without asking.
ask_transcription() {
    ASSEMBLYAI_KEY=""
    [ "${NO_TRANSCRIPTION:-false}" = true ] && return
    if gcloud secrets describe pablo-assemblyai-api-key --project="$PROJECT_ID" >/dev/null 2>&1; then
        return
    fi
    echo
    echo "Pablo can transcribe sessions with AssemblyAI, under your own signed BAA"
    echo "with AssemblyAI: https://www.assemblyai.com/legal/business-associate-agreement"
    local answer
    read -rp "Turn on transcription? You can add it later. [y/N] " answer
    [[ "$answer" =~ ^[Yy] ]] || return 0
    read -rsp "AssemblyAI API key (hidden): " ASSEMBLYAI_KEY
    echo
}

# Sets TRANSCRIPTION_PROVIDER and TRANSCRIPTION_ENABLED; stores a key given
# to ask_transcription.
ensure_transcription() {
    TRANSCRIPTION_PROVIDER=assemblyai
    if [ -n "${ASSEMBLYAI_KEY:-}" ]; then
        printf '%s' "$ASSEMBLYAI_KEY" | put_secret pablo-assemblyai-api-key
        unset ASSEMBLYAI_KEY
    fi
    if secret_exists pablo-assemblyai-api-key && [ "${NO_TRANSCRIPTION:-false}" != true ]; then
        TRANSCRIPTION_ENABLED=true
    else
        TRANSCRIPTION_ENABLED=false
    fi
}
