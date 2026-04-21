# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HIPAA § 164.308(a)(1)(ii)(D) — scheduled audit log review.

Runs daily (Cloud Scheduler cron) inside a Cloud Run Job. Queries the
last N hours of `audit_logs`, asks Claude (via Vertex AI — GCP BAA
covers it) to narrate anomalies, writes the timestamped report to a
retention-locked GCS bucket.

On a HIGH-severity finding the job emits a structured ``ERROR``-level
log entry that an operator-configured Cloud Monitoring log-based alert
policy can use to send email (see ``scripts/monitoring/setup.sh``).

The payload sent to Claude is PHI-free by contract (enforced by
AuditRepository.metadata_for_review()). Don't loosen that without
re-evaluating the BAA posture.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("hipaa_log_review")

SYSTEM_PROMPT = """\
You are a HIPAA compliance analyst reviewing 24 hours of audit log
metadata from a therapy documentation platform. The input is PHI-free:
opaque UUIDs, timestamps, action strings, IPs, user agents, and
field-name diffs.

The payload has two parts:

1. ``entries`` — per-row audit data enriched with three booleans:
   - ``is_novel_user_patient``: true when this user has >= 7 days of
     prior audit history AND has NOT accessed this patient in the
     preceding 90 days AND did not create that patient in the same
     window. Workhorse insider-snooping signal.
   - ``is_same_last_name``: true when the user's surname matches the
     patient's last name. High-signal relationship flag — a therapist
     accessing a potential relative's record should always be noted,
     even if treatment is legitimate. Usually MEDIUM severity unless
     combined with other flags.
   - ``is_no_treatment_relationship``: true when a PATIENT_VIEWED or
     SESSION_VIEWED happened but there's no scheduled appointment
     within ±7 days and no finalized session within ±1 day, AND the
     patient has existed > 14 days, AND the user has >= 5 historical
     appointments (system warmup). Strong insider-snooping signal.

2. ``user_aggregates`` — per-user alerts that don't fit the per-row
   shape. Each has an ``alert`` type:
   - ``bulk_delete``: user deleted > 3 patients in the window.
   - ``high_export_rate``: user's export count today exceeds their
     own 90-day P95 baseline (only fires with >= 14 days of baseline).

Warmup note: a brand-new install, a first-week user, or a user
returning after > 7 days away will have most flags FALSE. That means
"we don't have enough history to judge," NOT "all clear." If most
rows are flag-less, say so in the report and focus on the other
anomaly types below.

Your job is to narrate anomalies a human reviewer would care about.
Look for:
- Off-hours PHI access (02:00-05:00 local)
- Bulk reads by one user in a short window
- Rows where any of ``is_novel_user_patient``, ``is_same_last_name``,
  or ``is_no_treatment_relationship`` are true
- Any ``user_aggregates`` alerts (bulk_delete, high_export_rate)
- Cross-tenant patterns (if multiple user_ids touch the same patient_id)
- Unusual action sequences (e.g. LIST -> VIEW -> EXPORT chains)

Output a markdown report with sections:
## Summary (one paragraph)
## Anomalies (numbered, each with: severity HIGH/MEDIUM/LOW, evidence, recommended action)
## No-op confirmations (things you checked and found nothing unusual)

Reference users/patients by UUID only; never invent identifying
information. If the input is empty or too sparse, say so and stop.
"""


MONTHLY_WINDOW_HOURS = 24 * 30


def run(
    window_hours: int = 24,
    gcs_bucket: str | None = None,
) -> int:
    """Entry point. Returns exit code (0 on success, non-zero on failure).

    Set REVIEW_WINDOW_HOURS=720 (30 days) to run the monthly rollup
    review instead of the daily — same code, wider lens. The prompt
    detects long windows and asks the model to look for slow-burn
    patterns instead of point-in-time anomalies.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    gcs_bucket = gcs_bucket or os.environ.get("COMPLIANCE_REPORT_BUCKET")
    window_hours = int(os.environ.get("REVIEW_WINDOW_HOURS", window_hours))

    review_mode = "monthly" if window_hours >= MONTHLY_WINDOW_HOURS else "daily"
    logger.info(
        "Starting HIPAA log review — window_hours=%d mode=%s", window_hours, review_mode
    )

    payload = _load_review_payload(window_hours)
    logger.info(
        "Loaded %d audit rows + %d user aggregates for review",
        len(payload["entries"]),
        len(payload["user_aggregates"]),
    )

    if not payload["entries"] and not payload["user_aggregates"]:
        logger.info("No audit activity in window — skipping model call")
        report = "# HIPAA Log Review\n\nNo audit activity in the review window.\n"
        severity = "NONE"
    else:
        report = _ask_claude(payload, review_mode=review_mode)
        severity = _parse_severity(report)

    report_path = _write_report(report, gcs_bucket, review_mode=review_mode)
    logger.info("Report written to %s (severity=%s)", report_path, severity)

    if severity == "HIGH":
        _notify_high_finding(report_path)

    return 0


def _load_review_payload(window_hours: int) -> dict[str, Any]:
    """Compose the full review payload from all relevant repositories."""
    from ..db import create_standalone_session  # noqa: PLC0415
    from ..repositories.postgres.appointment import (  # noqa: PLC0415
        PostgresAppointmentRepository,
    )
    from ..repositories.postgres.audit import PostgresAuditRepository  # noqa: PLC0415
    from ..repositories.postgres.patient import PostgresPatientRepository  # noqa: PLC0415
    from ..repositories.postgres.session import (  # noqa: PLC0415
        PostgresTherapySessionRepository,
    )
    from ..repositories.postgres.user import PostgresUserRepository  # noqa: PLC0415
    from ..services.audit_review_service import AuditReviewService  # noqa: PLC0415

    session = create_standalone_session()
    try:
        service = AuditReviewService(
            audit_repo=PostgresAuditRepository(session),
            patient_repo=PostgresPatientRepository(session),
            user_repo=PostgresUserRepository(session),
            appointment_repo=PostgresAppointmentRepository(session),
            session_repo=PostgresTherapySessionRepository(session),
        )
        payload = service.compute_payload(window_hours=window_hours)
        return payload.to_dict()
    finally:
        session.close()


def _ask_claude(payload: dict[str, Any], review_mode: str = "daily") -> str:
    """Send payload to Claude on Vertex AI and return the markdown report.

    Uses the Anthropic SDK's Vertex adapter — inference runs inside GCP,
    so PHI (if it ever leaks into the payload despite the repo guard)
    stays under the GCP BAA.
    """
    system_prompt = SYSTEM_PROMPT
    if review_mode == "monthly":
        system_prompt += (
            "\n\nMONTHLY ROLLUP MODE: the window is 30 days, not 24 hours. "
            "Focus on slow-burn patterns that daily reviews would miss — e.g., a user "
            "steadily accumulating access to new patients over weeks, gradual shifts "
            "in hour-of-day distribution, sustained elevated export rates. Point-in-time "
            "flags still matter but lean heavier on trends."
        )
    try:
        from anthropic import AnthropicVertex  # type: ignore[import-not-found]  # noqa: PLC0415
        from anthropic.types import TextBlock  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError:
        logger.exception(
            "anthropic package not installed; add `anthropic[vertex]` to pyproject."
        )
        raise

    region = os.environ.get("VERTEX_REGION", "us-east5")
    project = os.environ.get("GCP_PROJECT_ID") or os.environ["GOOGLE_CLOUD_PROJECT"]
    model = os.environ.get("HIPAA_REVIEW_MODEL", "claude-sonnet-4-6")

    client = AnthropicVertex(region=region, project_id=project)
    user_content = (
        "Review the following audit log entries:\n\n"
        f"```json\n{json.dumps(payload, indent=2)}\n```"
    )
    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )
    parts = [b.text for b in resp.content if isinstance(b, TextBlock)]
    return "\n".join(parts) or "# HIPAA Log Review\n\n(empty model response)\n"


def _parse_severity(report: str) -> str:
    """Extract highest severity token from the markdown report."""
    for token in ("HIGH", "MEDIUM", "LOW"):
        if token in report:
            return token
    return "NONE"


def _write_report(report: str, gcs_bucket: str | None, review_mode: str = "daily") -> str:
    """Write report to GCS if configured, else print to stdout. Returns a URI."""
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    filename = f"hipaa-log-review/{review_mode}/{ts}.md"

    if not gcs_bucket:
        logger.warning("COMPLIANCE_REPORT_BUCKET unset — printing report to stdout")
        sys.stdout.write(report + "\n")
        return f"stdout://{filename}"

    try:
        from google.cloud import storage  # type: ignore[attr-defined]  # noqa: PLC0415
    except ImportError:
        logger.exception(
            "google-cloud-storage not installed; add to pyproject main deps."
        )
        raise

    client = storage.Client()
    blob = client.bucket(gcs_bucket).blob(filename)
    blob.upload_from_string(report, content_type="text/markdown")
    return f"gs://{gcs_bucket}/{filename}"


def _notify_high_finding(report_path: str) -> None:
    """Emit a structured ERROR log so Cloud Monitoring's log-based alert fires.

    Operators wire the email channel in ``scripts/monitoring/setup.sh``.
    An optional secondary channel is available: if ``ALERT_WEBHOOK_URL``
    is set (Slack/Discord/generic incoming-webhook/paging vendor), the
    report URL is POSTed there as JSON. Silent no-op if unset.
    """
    payload = {
        "severity": "ERROR",
        "message": f"alert_type=hipaa_review_high report={report_path} — review findings",
        "alert_type": "hipaa_review_high",
        "report": report_path,
    }
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()
    webhook = os.environ.get("ALERT_WEBHOOK_URL")
    if webhook:
        _post_webhook(webhook, report_path, alert_source="hipaa-log-review")


HTTP_REDIRECT_THRESHOLD = 300


def _post_webhook(webhook_url: str, report_path: str, alert_source: str) -> None:
    """POST a minimal JSON body to the configured webhook.

    Body shape is generic — Slack, Discord, PagerDuty Events v2, and most
    other webhook sinks accept arbitrary JSON. Operator picks the sink
    by setting ALERT_WEBHOOK_URL; this function doesn't care which.
    """
    import urllib.request  # noqa: PLC0415

    body = json.dumps(
        {
            "source": alert_source,
            "severity": "HIGH",
            "text": f"{alert_source}: HIGH severity finding — {report_path}",
            "report": report_path,
        }
    ).encode()
    req = urllib.request.Request(  # noqa: S310 — operator-provided webhook URL
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            if resp.status >= HTTP_REDIRECT_THRESHOLD:
                logger.error("Webhook POST failed: %s", resp.status)
    except Exception:
        logger.exception("Webhook POST raised — continuing")


if __name__ == "__main__":
    sys.exit(run())
