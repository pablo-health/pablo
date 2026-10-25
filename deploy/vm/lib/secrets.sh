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
    [ "${SKIP_GOOGLE_SIGNIN:-false}" = true ] && return
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
       https://${PABLO_HOST}
     Authorized redirect URIs:
       https://${PABLO_HOST}/__/auth/handler
       https://${PROJECT_ID}.firebaseapp.com/__/auth/handler
       https://${PABLO_HOST}/dashboard/settings/calendar
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
