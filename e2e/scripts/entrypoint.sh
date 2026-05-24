#!/usr/bin/env bash
#
# Cloud Run Job entrypoint for the pablo OSS e2e runner.
#
# Responsibilities:
#   1. Run Playwright tests.
#   2. Upload the HTML report + JUnit + traces to GCS (best-effort).
#   3. Exit with Playwright's exit code so the job status reflects test
#      outcome, not artifact-upload outcome.
#
# Env vars consumed:
#   PLAYWRIGHT_BASE_URL    target app (no default — set by Cloud Run Job)
#   FIREBASE_API_KEY       from Secret Manager (e2e-oss-pinned-firebase-api-key)
#   TEST_PASSWORD          from Secret Manager (e2e-oss-pinned-password)
#   PINNED_EMAIL           from Secret Manager (e2e-oss-pinned-email)
#   E2E_ARTIFACTS_BUCKET   gs://... destination for reports
#   RUN_ID                 unique run id (gh run id, or generated)
#
set -uo pipefail

: "${RUN_ID:=$(date -u +%Y%m%dT%H%M%SZ)-$$}"
export RUN_ID

if [[ -z "${PLAYWRIGHT_BASE_URL:-}" ]]; then
  echo "[entrypoint] FATAL: PLAYWRIGHT_BASE_URL not set" >&2
  exit 2
fi

echo "[entrypoint] PLAYWRIGHT_BASE_URL=$PLAYWRIGHT_BASE_URL"
echo "[entrypoint] RUN_ID=$RUN_ID"

./node_modules/.bin/playwright test "$@"
test_exit=$?
echo "[entrypoint] playwright exit code: $test_exit"

if [[ -n "${E2E_ARTIFACTS_BUCKET:-}" ]]; then
  echo "[entrypoint] uploading artifacts to gs://${E2E_ARTIFACTS_BUCKET}/${RUN_ID}/"
  ./node_modules/.bin/tsx scripts/upload-artifacts.ts || true
else
  echo "[entrypoint] E2E_ARTIFACTS_BUCKET unset; skipping upload"
fi

exit "$test_exit"
