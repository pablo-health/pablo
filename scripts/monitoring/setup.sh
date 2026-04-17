#!/bin/bash
#
# Pablo Monitoring — GCP-native, no third-party accounts
#
# Creates uptime checks and alert policies using only GCP services that
# are covered by your existing GCP BAA and included in Cloud Run / GCP
# startup credits. Runs in ~30 seconds, needs no extra signups.
#
# Includes a log-based alert that fires whenever the HIPAA log-review
# or pentest Cloud Run Jobs log a HIGH-severity finding — routes to
# the same email channel.
#
# Idempotent — safe to re-run. Creates resources only if missing.
#
# Usage: ./scripts/monitoring/setup.sh <project-id> <backend-url> <frontend-url> <notify-email>

set -euo pipefail

PROJECT_ID="${1:-${PABLO_PROJECT:-}}"
BACKEND_URL="${2:-${PABLO_BACKEND_URL:-}}"
FRONTEND_URL="${3:-${PABLO_FRONTEND_URL:-}}"
NOTIFY_EMAIL="${4:-${PABLO_NOTIFY_EMAIL:-}}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
RESET='\033[0m'

info()  { echo -e "${GREEN}==>${RESET} $*"; }
warn()  { echo -e "${YELLOW}!!${RESET} $*"; }
die()   { echo -e "${RED}xx${RESET} $*" >&2; exit 1; }

[[ -z "$PROJECT_ID"    ]] && die "Usage: $0 <project-id> <backend-url> <frontend-url> <notify-email>"
[[ -z "$BACKEND_URL"   ]] && die "Missing backend URL"
[[ -z "$FRONTEND_URL"  ]] && die "Missing frontend URL"
[[ -z "$NOTIFY_EMAIL"  ]] && die "Missing notify email"

info "Monitoring setup (Simple tier) for project: $PROJECT_ID"
info "Backend:  $BACKEND_URL"
info "Frontend: $FRONTEND_URL"
info "Alerts:   $NOTIFY_EMAIL"

gcloud services enable monitoring.googleapis.com --project="$PROJECT_ID" >/dev/null

# ---------- 1. Email notification channel (idempotent by display name) ----------
info "Ensuring email notification channel exists"
CHANNEL_NAME="Pablo Solo alerts"
CHANNEL_ID=$(gcloud alpha monitoring channels list \
    --project="$PROJECT_ID" \
    --filter="displayName=\"$CHANNEL_NAME\"" \
    --format="value(name)" 2>/dev/null | head -n1 || true)

if [[ -z "$CHANNEL_ID" ]]; then
    CHANNEL_ID=$(gcloud alpha monitoring channels create \
        --project="$PROJECT_ID" \
        --display-name="$CHANNEL_NAME" \
        --type=email \
        --channel-labels="email_address=$NOTIFY_EMAIL" \
        --format="value(name)")
    info "Created notification channel: $CHANNEL_ID"
else
    info "Reusing notification channel: $CHANNEL_ID"
fi

# ---------- 2. Uptime checks ----------
ensure_uptime_check() {
    local display_name="$1"
    local url="$2"
    local path="$3"

    local host="${url#https://}"
    host="${host%%/*}"

    local existing
    existing=$(gcloud monitoring uptime list-configs \
        --project="$PROJECT_ID" \
        --filter="displayName=\"$display_name\"" \
        --format="value(name)" 2>/dev/null | head -n1 || true)

    if [[ -n "$existing" ]]; then
        info "Uptime check exists: $display_name"
        return
    fi

    gcloud monitoring uptime create "$display_name" \
        --project="$PROJECT_ID" \
        --resource-type=uptime-url \
        --resource-labels="host=$host,project_id=$PROJECT_ID" \
        --path="$path" \
        --port=443 \
        --protocol=https \
        --period=1 \
        --timeout=10 >/dev/null
    info "Created uptime check: $display_name"
}

ensure_uptime_check "Pablo Backend /healthz"  "$BACKEND_URL"  "/healthz"
ensure_uptime_check "Pablo Frontend /"         "$FRONTEND_URL" "/"

# ---------- 3. Alert policies ----------
# Each policy is rendered from a heredoc template and applied via
# `gcloud alpha monitoring policies create-or-update` equivalents
# (create if missing). We look up by displayName for idempotency.

ensure_policy() {
    local display_name="$1"
    local policy_json="$2"

    local existing
    existing=$(gcloud alpha monitoring policies list \
        --project="$PROJECT_ID" \
        --filter="displayName=\"$display_name\"" \
        --format="value(name)" 2>/dev/null | head -n1 || true)

    if [[ -n "$existing" ]]; then
        info "Alert policy exists: $display_name"
        return
    fi

    echo "$policy_json" | gcloud alpha monitoring policies create \
        --project="$PROJECT_ID" \
        --policy-from-file=/dev/stdin >/dev/null
    info "Created alert policy: $display_name"
}

# Backend 5xx rate > 1% over 5 min
ensure_policy "Pablo backend 5xx rate" "$(cat <<JSON
{
  "displayName": "Pablo backend 5xx rate",
  "combiner": "OR",
  "conditions": [{
    "displayName": "5xx > 1% for 5m",
    "conditionThreshold": {
      "filter": "resource.type=\"cloud_run_revision\" AND metric.type=\"run.googleapis.com/request_count\" AND metric.labels.response_code_class=\"5xx\"",
      "aggregations": [{
        "alignmentPeriod": "60s",
        "perSeriesAligner": "ALIGN_RATE"
      }],
      "comparison": "COMPARISON_GT",
      "thresholdValue": 0.01,
      "duration": "300s",
      "trigger": {"count": 1}
    }
  }],
  "notificationChannels": ["$CHANNEL_ID"],
  "alertStrategy": {"autoClose": "1800s"}
}
JSON
)"

# Cloud SQL disk > 80%
ensure_policy "Pablo Cloud SQL disk > 80%" "$(cat <<JSON
{
  "displayName": "Pablo Cloud SQL disk > 80%",
  "combiner": "OR",
  "conditions": [{
    "displayName": "disk utilization > 80%",
    "conditionThreshold": {
      "filter": "resource.type=\"cloudsql_database\" AND metric.type=\"cloudsql.googleapis.com/database/disk/utilization\"",
      "aggregations": [{
        "alignmentPeriod": "300s",
        "perSeriesAligner": "ALIGN_MEAN"
      }],
      "comparison": "COMPARISON_GT",
      "thresholdValue": 0.80,
      "duration": "600s",
      "trigger": {"count": 1}
    }
  }],
  "notificationChannels": ["$CHANNEL_ID"],
  "alertStrategy": {"autoClose": "3600s"}
}
JSON
)"

# Uptime check failure (covers both backend /healthz and frontend)
ensure_policy "Pablo uptime check failure" "$(cat <<JSON
{
  "displayName": "Pablo uptime check failure",
  "combiner": "OR",
  "conditions": [{
    "displayName": "Any uptime check failing",
    "conditionThreshold": {
      "filter": "resource.type=\"uptime_url\" AND metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\"",
      "aggregations": [{
        "alignmentPeriod": "60s",
        "perSeriesAligner": "ALIGN_NEXT_OLDER",
        "crossSeriesReducer": "REDUCE_COUNT_FALSE",
        "groupByFields": ["resource.labels.host", "resource.labels.project_id"]
      }],
      "comparison": "COMPARISON_GT",
      "thresholdValue": 1,
      "duration": "60s",
      "trigger": {"count": 1}
    }
  }],
  "notificationChannels": ["$CHANNEL_ID"],
  "alertStrategy": {"autoClose": "1800s"}
}
JSON
)"

# Compliance routine HIGH findings (HIPAA log review + pentest)
# Fires when those jobs emit a structured ERROR log with
# jsonPayload.alert_type matching. Routes to the same email channel.
ensure_policy "Pablo compliance routine HIGH finding" "$(cat <<JSON
{
  "displayName": "Pablo compliance routine HIGH finding",
  "combiner": "OR",
  "conditions": [{
    "displayName": "Compliance job logged a HIGH finding",
    "conditionMatchedLog": {
      "filter": "resource.type=\"cloud_run_job\" AND severity=\"ERROR\" AND jsonPayload.alert_type=~\"hipaa_review_high|pentest_high\""
    }
  }],
  "notificationChannels": ["$CHANNEL_ID"],
  "alertStrategy": {
    "notificationRateLimit": {"period": "300s"},
    "autoClose": "86400s"
  }
}
JSON
)"

info "Simple monitoring tier configured."
info "Alerts will be sent to: $NOTIFY_EMAIL"
