# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Weekly synthetic heartbeat — verifies the compliance alert pipeline.

Runs on Cloud Scheduler once a week. Emits a structured ``ERROR``-level
log entry with ``alert_type=pipeline_heartbeat`` and writes a
timestamped marker to the compliance GCS bucket. The operator's Cloud
Monitoring log-based alert routes it to email.

If the operator stops receiving the weekly heartbeat email, something
along the chain broke — missed scheduler, failed job, logging
misconfigured, alert policy deleted, or email channel inactive. A
silent compliance pipeline is the worst kind of compliance pipeline.

The heartbeat intentionally does NOT call the LLM or query the audit
table. Its only job is to exercise the log → alert → email path
cheaply and reliably.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import UTC, datetime

logger = logging.getLogger("pipeline_heartbeat")


def run() -> int:
    """Entry point. Always exits 0 unless something fundamental is broken."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    now_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    logger.info("Pablo compliance pipeline heartbeat — %s", now_iso)

    gcs_bucket = os.environ.get("COMPLIANCE_REPORT_BUCKET")
    if gcs_bucket:
        _write_heartbeat_marker(gcs_bucket, now_iso)

    # The structured ERROR log is what the log-based alert policy
    # matches on — same mechanism as real HIGH findings, different
    # alert_type so operators can filter the drill emails separately.
    logger.error(
        "alert_type=pipeline_heartbeat ts=%s — weekly drill, pipeline is alive",
        now_iso,
        extra={"alert_type": "pipeline_heartbeat", "timestamp": now_iso},
    )
    return 0


def _write_heartbeat_marker(gcs_bucket: str, now_iso: str) -> None:
    """Write a small timestamp file to GCS to confirm the upload path works."""
    try:
        from google.cloud import storage  # type: ignore[attr-defined]  # noqa: PLC0415
    except ImportError:
        logger.warning("google-cloud-storage not installed; skipping heartbeat marker")
        return

    client = storage.Client()
    blob_name = f"heartbeat/{now_iso.replace(':', '-')}.txt"
    body = (
        f"Pablo compliance pipeline heartbeat\n"
        f"timestamp: {now_iso}\n"
        f"purpose: confirms Cloud Run Job runs, GCS write works, and "
        f"log-based alert delivers email.\n"
    )
    try:
        client.bucket(gcs_bucket).blob(blob_name).upload_from_string(
            body, content_type="text/plain"
        )
    except Exception:
        logger.exception("Heartbeat marker upload failed — pipeline partially broken")


if __name__ == "__main__":
    sys.exit(run())
