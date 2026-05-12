#!/usr/bin/env bash
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
#
# setup-hipaa-audit-sink.sh
#
# Configures HIPAA-strong 6-year retention Log Sinks for a Pablo GCP project.
# Creates a retention-locked Archive-class GCS bucket and two Log Sinks that
# feed it:
#
#   1. hipaa-admin-activity-6y  — GCP Admin Activity (control-plane events).
#      Legal anchor: § 164.308(a)(1)(ii)(D) Information System Activity
#      Review requires retention of records of system activity;
#      § 164.316(b)(2)(i) sets the 6-year floor. GCP's _Required bucket
#      retains Admin Activity for only 400 days, leaving the gap.
#
#   2. hipaa-app-audit-6y       — Pablo's application audit events
#      (PHI access logged by AuditService). Defense-in-depth on top of
#      the canonical Postgres audit_logs table. Provides § 164.312(c)(2)
#      integrity protection: once a log line lands in the retention-locked
#      bucket, it cannot be modified or deleted for the full window, even
#      by a project owner.
#
# This script is idempotent — safe to re-run.
#
# Usage:
#   ./setup-hipaa-audit-sink.sh <PROJECT_ID> [--lock]
#
# By default, retention is set but NOT locked. Pass --lock to make the
# retention policy irreversible (recommended for production HIPAA evidence).

set -euo pipefail

PROJECT_ID="${1:-}"
LOCK_FLAG="${2:-}"

if [[ -z "$PROJECT_ID" ]]; then
    echo "Usage: $0 <PROJECT_ID> [--lock]" >&2
    exit 1
fi

BUCKET_NAME="${PROJECT_ID}-hipaa-audit-6y"
BUCKET="gs://${BUCKET_NAME}"
RETENTION_DAYS=2255  # ~6 years + 60-day buffer

ADMIN_SINK="hipaa-admin-activity-6y"
ADMIN_FILTER='LOG_ID("cloudaudit.googleapis.com/activity") OR LOG_ID("externalaudit.googleapis.com/activity")'

APP_SINK="hipaa-app-audit-6y"
APP_FILTER='logName="projects/'"$PROJECT_ID"'/logs/pablo.audit_events"'

echo "==> Configuring HIPAA audit sinks for project: ${PROJECT_ID}"

# Step 1: Create Archive-class bucket (idempotent)
if gsutil ls -b -p "$PROJECT_ID" "$BUCKET" &>/dev/null; then
    echo "    [skip] Bucket ${BUCKET} already exists"
else
    echo "    [create] Bucket ${BUCKET} (Archive, us)"
    gsutil mb -p "$PROJECT_ID" -l us -c ARCHIVE "$BUCKET"
fi

# Step 2: Set retention (idempotent — gsutil retention set re-asserts)
CURRENT_RETENTION=$(gsutil retention get "$BUCKET" 2>&1 | grep -oE 'Duration: [0-9]+' | awk '{print $2}' || echo "0")
if [[ "$CURRENT_RETENTION" == "$RETENTION_DAYS" ]]; then
    echo "    [skip] Retention already set to ${RETENTION_DAYS}d"
else
    echo "    [set]  Retention to ${RETENTION_DAYS}d"
    gsutil retention set "${RETENTION_DAYS}d" "$BUCKET"
fi

# Step 3: Create both sinks (idempotent). Function factored out because
# we configure two sinks against the same bucket — one for GCP control
# plane, one for Pablo's application audit events.
create_sink() {
    local sink_name="$1"
    local log_filter="$2"

    if gcloud logging sinks describe "$sink_name" --project="$PROJECT_ID" &>/dev/null; then
        echo "    [skip] Sink ${sink_name} already exists"
    else
        echo "    [create] Sink ${sink_name}"
        gcloud logging sinks create "$sink_name" \
            "storage.googleapis.com/${BUCKET_NAME}" \
            --log-filter="$log_filter" \
            --project="$PROJECT_ID"
    fi

    # Grant the sink's writer identity Storage Object Creator (idempotent —
    # gsutil iam ch is a no-op if the binding already exists).
    local writer
    writer=$(gcloud logging sinks describe "$sink_name" --project="$PROJECT_ID" --format='value(writerIdentity)')
    echo "    [grant] ${writer} -> roles/storage.objectCreator on bucket"
    gsutil iam ch "${writer}:roles/storage.objectCreator" "$BUCKET"
}

create_sink "$ADMIN_SINK" "$ADMIN_FILTER"
create_sink "$APP_SINK" "$APP_FILTER"

# Step 4 (optional): Lock retention — IRREVERSIBLE.
if [[ "$LOCK_FLAG" == "--lock" ]]; then
    LOCKED=$(gsutil retention get "$BUCKET" 2>&1 | grep -c "LOCKED" || true)
    if [[ "$LOCKED" -gt 0 ]]; then
        echo "    [skip] Retention already locked"
    else
        echo "    [lock] Locking retention policy (IRREVERSIBLE)..."
        gsutil retention lock "$BUCKET"
    fi
else
    echo ""
    echo "    NOTE: Retention is set but NOT locked. For production HIPAA"
    echo "    evidence, re-run with --lock to make the policy irreversible:"
    echo "      $0 ${PROJECT_ID} --lock"
fi

echo ""
echo "==> Done. Verify with:"
echo "    gcloud logging sinks list --project=${PROJECT_ID}"
echo "    gsutil retention get ${BUCKET}"
