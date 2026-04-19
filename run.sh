#!/usr/bin/env bash
# Drop into an interactive shell inside the pentest container.
#
# From inside you can run any of:
#   gemini --yolo -p "/pentest run the weekly test now"
#   claude -p "/pentest run the weekly test now" --dangerously-skip-permissions
#   python3.13 /app/backend/app/jobs/pentest_runner.py
#   nuclei / ffuf / sqlmap / semgrep / trivy / testssl.sh / ...
#
# Required: GCP_PROJECT_ID set, `gcloud auth application-default login` run recently.

set -euo pipefail

: "${GCP_PROJECT_ID:?Set GCP_PROJECT_ID first (e.g. export GCP_PROJECT_ID=pablohealth-oss)}"
: "${VERTEX_REGION:=global}"
: "${PENTEST_CLI:=gemini}"
: "${GEMINI_MODEL:=gemini-3.1-pro-preview}"
: "${IMAGE:=pablo-pentest:local}"
: "${PLATFORM:=linux/amd64}"
: "${PENTEST_BOOTSTRAP:=1}"       # create+enroll ephemeral Firebase user, export creds
: "${PENTEST_FRONTEND_URL:=}"     # required iff PENTEST_BOOTSTRAP=1

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
ADC_FILE="$HOME/.config/gcloud/application_default_credentials.json"
[[ -f "$ADC_FILE" ]] || { echo "No ADC at $ADC_FILE — run: gcloud auth application-default login"; exit 1; }

# When bootstrap is enabled, the in-container startup runs
# `python3.13 -m app.jobs.pentest_identity`, evals its shell exports,
# and drops you into bash with PENTEST_TEST_* already set. The trap
# deletes the ephemeral Firebase user on exit (even on ^C) so we
# never leave residue in Identity Platform.
if [[ "$PENTEST_BOOTSTRAP" == "1" ]]; then
  [[ -n "$PENTEST_FRONTEND_URL" ]] || {
    echo "PENTEST_FRONTEND_URL unset (needed to fetch firebaseApiKey via /api/config)."
    echo "Set it to the frontend Cloud Run URL and re-run, or set PENTEST_BOOTSTRAP=0."
    exit 1
  }
  STARTUP_CMD='
    set -euo pipefail
    cleanup() {
      if [[ -n "${PENTEST_TEST_UID:-}" ]]; then
        python3.13 -c "
from app.jobs import pentest_identity
from firebase_admin import auth
pentest_identity._ensure_firebase_initialized(\"'"$GCP_PROJECT_ID"'\")
try: auth.delete_user(\"$PENTEST_TEST_UID\")
except Exception: pass
" 2>/dev/null || true
      fi
    }
    trap cleanup EXIT INT TERM
    echo "Bootstrapping ephemeral pentest Firebase user..."
    eval "$(cd /app/backend && python3.13 -m app.jobs.pentest_identity)"
    echo "OK — signed in as $PENTEST_TEST_EMAIL (uid=$PENTEST_TEST_UID)"
    exec /bin/bash
  '
  ENTRYPOINT_ARGS=(--entrypoint /bin/bash)
  STARTUP_ARGS=(-c "$STARTUP_CMD")
else
  ENTRYPOINT_ARGS=(--entrypoint /bin/bash)
  STARTUP_ARGS=()
fi

# gcloud config at a non-standard path so it doesn't collide with other
# CLI tools (nuclei/uncover/etc.) that expect to create their own dirs
# under the nonroot user's ~/.config. CLOUDSDK_CONFIG redirects gcloud.
exec docker run --rm -it \
  --platform "$PLATFORM" \
  -v "$HOME/.config/gcloud:/gcloud-config" \
  -v "$REPO_ROOT/backend:/app/backend:ro" \
  -v "$REPO_ROOT/.claude/skills/pentest:/workspace/.claude/skills/pentest:ro" \
  -v "$REPO_ROOT/.gemini/skills/pentest:/workspace/.gemini/skills/pentest:ro" \
  -e CLOUDSDK_CONFIG=/gcloud-config \
  -e GOOGLE_APPLICATION_CREDENTIALS=/gcloud-config/application_default_credentials.json \
  -e CLOUDSDK_CORE_PROJECT="$GCP_PROJECT_ID" \
  -e GCP_PROJECT_ID="$GCP_PROJECT_ID" \
  -e GOOGLE_CLOUD_PROJECT="$GCP_PROJECT_ID" \
  -e VERTEX_REGION="$VERTEX_REGION" \
  -e GOOGLE_CLOUD_LOCATION="$VERTEX_REGION" \
  -e ANTHROPIC_VERTEX_PROJECT_ID="$GCP_PROJECT_ID" \
  -e CLAUDE_CODE_USE_VERTEX=1 \
  -e GOOGLE_GENAI_USE_VERTEXAI=true \
  -e PENTEST_CLI="$PENTEST_CLI" \
  -e GEMINI_MODEL="$GEMINI_MODEL" \
  -e PENTEST_FRONTEND_URL="$PENTEST_FRONTEND_URL" \
  "${ENTRYPOINT_ARGS[@]}" \
  "$IMAGE" "${STARTUP_ARGS[@]}"
