#!/bin/bash
#
# Pablo Auto-Updater — OPT-IN, fail-safe
#
# Installs a nightly Cloud Run Job that:
#   1. Checks ghcr.io for a newer patch-version image (vX.Y.Z -> vX.Y.Z+1 only)
#   2. Deploys it to Cloud Run with --no-traffic (shadow revision)
#   3. Runs smoke tests against the shadow
#   4. If green, shifts traffic 100% and notifies; if red, rolls back and opens a GitHub issue
#
# Never auto-updates minor or major versions — those can require companion
# app updates and therapist action. Failure mode is "therapist stays on
# current version, sees an admin UI banner next morning" — never broken
# mid-session.
#
# Usage:
#   ./scripts/routines/auto-updater-opt-in.sh <project-id> [--enable|--disable|--status]
#
# This script is NOT called by setup-solo.sh automatically. Therapists
# opt in by running it after install.

set -euo pipefail

PROJECT_ID="${1:-${PABLO_PROJECT:-}}"
ACTION="${2:---status}"

[[ -z "$PROJECT_ID" ]] && { echo "Usage: $0 <project-id> [--enable|--disable|--status]"; exit 1; }

REGION="${PABLO_REGION:-us-central1}"
SA="pablo-updater@${PROJECT_ID}.iam.gserviceaccount.com"

case "$ACTION" in
  --enable)
    echo "Enabling opt-in auto-updater for $PROJECT_ID"
    echo
    echo "This will create:"
    echo "  - Service account: $SA"
    echo "  - Cloud Run Job:   pablo-auto-updater"
    echo "  - Scheduler:       nightly 03:00 local"
    echo
    echo "The updater only applies PATCH-version updates (vX.Y.Z -> vX.Y.Z+1)."
    echo "Minor/major updates require you to re-run ./redeploy.sh manually."
    echo
    read -p "Proceed? [y/N]: " ok
    [[ "$ok" =~ ^[Yy]$ ]] || { echo "Cancelled."; exit 0; }
    # TODO: implement — intentionally a stub in v1.
    echo "[stub] Auto-updater enable logic not yet implemented in v1."
    echo "       Track progress: https://github.com/pablo-health/pablo/issues"
    ;;
  --disable)
    echo "Disabling auto-updater"
    gcloud scheduler jobs delete pablo-auto-updater-nightly \
      --project="$PROJECT_ID" --location="$REGION" --quiet 2>/dev/null || true
    gcloud run jobs delete pablo-auto-updater \
      --project="$PROJECT_ID" --region="$REGION" --quiet 2>/dev/null || true
    echo "Auto-updater disabled. You can continue updating manually via ./redeploy.sh."
    ;;
  --status|*)
    if gcloud scheduler jobs describe pablo-auto-updater-nightly \
        --project="$PROJECT_ID" --location="$REGION" >/dev/null 2>&1; then
        echo "auto-updater: ENABLED (runs nightly)"
    else
        echo "auto-updater: DISABLED"
        echo
        echo "Manual updates: git pull && ./redeploy.sh"
        echo "Enable opt-in:  $0 $PROJECT_ID --enable"
    fi
    ;;
esac
