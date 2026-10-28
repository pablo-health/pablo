# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
# shellcheck shell=bash
#
# The backend's and frontend's env files, from config.env values and Secret
# Manager. Sourced by pablo-update on the VM and by install.sh --target local,
# so the two run the same settings. Expects the config values (PROJECT_ID,
# REGION, PABLO_HOST, the buckets, FIREBASE_*, GOOGLE_CLIENT_ID,
# TRANSCRIPTION_*) and VERSION.

secret() { gcloud secrets versions access latest --secret="$1" --project="$PROJECT_ID"; }
optional_secret() { secret "$1" 2>/dev/null || true; }

# render_env_files <dir> <origin> <database host> <frontend's API URL>
#
# <origin> is the address clinicians and Cloud Tasks reach (https://...).
# Passkeys are scoped to PABLO_HOST (no port): a clinician who later moves to
# another address enrolls them again, and password plus authenticator still
# work meanwhile.
# <frontend's API URL> is where the frontend's server reaches the backend: it
# must arrive through Caddy, the only proxy the backend trusts for HTTPS.
render_env_files() {
    local dir="$1" origin="$2" db_host="$3" api_url="$4"
    # Read up front: a failed lookup inside the heredocs below would not stop
    # the script, and would write an empty secret.
    local db_password jwt_secret calendar_key assemblyai_key auth_secret cookie_key client_secret
    local forward_secret
    db_password="$(secret pablo-db-password)"
    jwt_secret="$(secret JWT_SECRET_KEY)"
    calendar_key="$(secret GOOGLE_CALENDAR_ENCRYPTION_KEY)"
    assemblyai_key="$(optional_secret pablo-assemblyai-api-key)"
    auth_secret="$(secret AUTH_SECRET)"
    cookie_key="$(secret AUTH_COOKIE_SIGNATURE_KEY)"
    # Lets the frontend server pass the end user's IP to the backend for audit
    # records. Optional: an install from before it existed has none until
    # install.sh runs again, and both services then record what they did before.
    forward_secret="$(optional_secret FRONTEND_FORWARD_SECRET)"
    # Empty when the install chose email and password only.
    client_secret="$(optional_secret GOOGLE_CLIENT_SECRET)"
    # The clinician's one OAuth client serves Google sign-in and Google Calendar.
    local calendar_enabled=false
    [ -z "$GOOGLE_CLIENT_ID" ] || calendar_enabled=true

    (
        umask 077
        cat > "${dir}/backend.env" <<ENV
ENVIRONMENT=production
PORT=8000
ENFORCE_HTTPS=true
TRUSTED_PROXY_IPS=172.30.0.10
GCP_PROJECT_ID=${PROJECT_ID}
GOOGLE_CLOUD_PROJECT=${PROJECT_ID}
FIREBASE_PROJECT_ID=${PROJECT_ID}
GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}
DATABASE_BACKEND=postgres
DATABASE_URL=postgresql://pablo:${db_password}@${db_host}:5432/pablo
USE_REDIS=true
REDIS_HOST=redis
RESTRICT_SIGNUPS=true
APP_URL=${origin}
BACKEND_BASE_URL=${origin}
CORS_ORIGINS=${origin}
WEBAUTHN_ORIGINS=${origin}
WEBAUTHN_RP_ID=${PABLO_HOST}
AI_MODEL=google:gemini-3.1-pro-preview
GOOGLE_GENAI_USE_VERTEXAI=true
GOOGLE_REGION=global
VERTEX_REGION=global
TRANSCRIPTION_ENABLED=${TRANSCRIPTION_ENABLED:-true}
TRANSCRIPTION_PROVIDER=${TRANSCRIPTION_PROVIDER}
TRANSCRIPTION_AUDIO_BUCKET=${AUDIO_BUCKET}
TRANSCRIPTION_QUEUE_LOCATION=${REGION}
CALENDAR_SYNC_TASK_LOCATION=${REGION}
TRANSCRIPTION_BACKEND_CALLBACK_URL=${origin}
FILE_STORAGE_PROVIDER=gcs
PATIENT_DOCUMENTS_GCS_BUCKET=${DOCUMENTS_BUCKET}
COMPLIANCE_REPORT_BUCKET=${COMPLIANCE_BUCKET}
PABLO_VERSION=${VERSION}
JWT_SECRET_KEY=${jwt_secret}
GOOGLE_CALENDAR_ENCRYPTION_KEY=${calendar_key}
GOOGLE_CALENDAR_CLIENT_ID=${GOOGLE_CLIENT_ID}
GOOGLE_CALENDAR_CLIENT_SECRET=${client_secret}
ASSEMBLYAI_API_KEY=${assemblyai_key}
FRONTEND_FORWARD_SECRET=${forward_secret}
ENV

        cat > "${dir}/frontend.env" <<ENV
PORT=3000
HOSTNAME=0.0.0.0
API_URL=${api_url}
PUBLIC_API_URL=${origin}
AUTH_URL=${origin}
APP_HOSTS=${PABLO_HOST}
DEV_MODE=false
DATA_MODE=api
ENABLE_LOCAL_AUTH=false
PASSKEYS_ENABLED=true
FIREBASE_PROJECT_ID=${PROJECT_ID}
GOOGLE_CLOUD_PROJECT=${PROJECT_ID}
GCLOUD_PROJECT=${PROJECT_ID}
NFAE_ALLOW_CLIENT_REFRESH_TOKEN=true
GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}
GOOGLE_CALENDAR_ENABLED=${calendar_enabled}
NEXT_PUBLIC_FIREBASE_API_KEY=${FIREBASE_API_KEY}
NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=${FIREBASE_AUTH_DOMAIN}
NEXT_PUBLIC_FIREBASE_PROJECT_ID=${PROJECT_ID}
NEXT_PUBLIC_FIREBASE_APP_ID=${FIREBASE_APP_ID}
AUTH_SECRET=${auth_secret}
AUTH_COOKIE_SIGNATURE_KEY=${cookie_key}
GOOGLE_CLIENT_SECRET=${client_secret}
FRONTEND_FORWARD_SECRET=${forward_secret}
ENV
    )
}
