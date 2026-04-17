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

Each row is pre-enriched with ``is_novel_user_patient``: true when this
user has >= 7 days of prior audit history AND has NOT accessed this
patient in the preceding 90 days AND did not create that patient in
the same window. This is the single novelty signal worth trusting —
IP and user-agent novelty were dropped because DHCP / mobile / VPN /
browser-update churn makes them noise.

Warmup note: a brand-new install, a first-week user, or a user
returning after > 7 days away will have ``is_novel_user_patient`` =
FALSE for every row — that means "we don't have enough history to
judge them yet," NOT "all clear." If most rows have it false, mention
in the report that the baseline is shallow and novelty checks aren't
meaningful yet; focus on the other anomaly types below.

Your job is to narrate anomalies a human reviewer would care about.
Look for:
- Off-hours PHI access (02:00-05:00 local)
- Bulk reads by one user in a short window
- Rows where ``is_novel_user_patient`` is true — users accessing
  patient records they haven't touched in 90d
- Rows where ``is_novel_user_ip`` or ``is_novel_user_agent`` is true —
  new infrastructure for an existing user (possible impossible-geo)
- Cross-tenant patterns (if multiple user_ids touch the same patient_id)
- Unusual action sequences (e.g. LIST -> VIEW -> EXPORT chains)

Output a markdown report with sections:
## Summary (one paragraph)
## Anomalies (numbered, each with: severity HIGH/MEDIUM/LOW, evidence, recommended action)
## No-op confirmations (things you checked and found nothing unusual)

Reference users/patients by UUID only; never invent identifying
information. If the input is empty or too sparse, say so and stop.
"""


def run(
    window_hours: int = 24,
    gcs_bucket: str | None = None,
) -> int:
    """Entry point. Returns exit code (0 on success, non-zero on failure)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    gcs_bucket = gcs_bucket or os.environ.get("COMPLIANCE_REPORT_BUCKET")
    window_hours = int(os.environ.get("REVIEW_WINDOW_HOURS", window_hours))

    logger.info("Starting HIPAA log review — window_hours=%d", window_hours)

    payload = _load_audit_metadata(window_hours)
    logger.info("Loaded %d audit rows for review", len(payload))

    if not payload:
        logger.info("No audit activity in window — skipping model call")
        report = "# HIPAA Log Review\n\nNo audit activity in the review window.\n"
        severity = "NONE"
    else:
        report = _ask_claude(payload)
        severity = _parse_severity(report)

    report_path = _write_report(report, gcs_bucket)
    logger.info("Report written to %s (severity=%s)", report_path, severity)

    if severity == "HIGH":
        _notify_high_finding(report_path)

    return 0


def _load_audit_metadata(window_hours: int) -> list[dict[str, Any]]:
    """Load last `window_hours` of audit rows as PHI-free dicts."""
    from ..db import create_standalone_session  # noqa: PLC0415
    from ..repositories.postgres.audit import PostgresAuditRepository  # noqa: PLC0415

    session = create_standalone_session()
    try:
        repo = PostgresAuditRepository(session)
        return repo.metadata_for_review(window_hours=window_hours)
    finally:
        session.close()


def _ask_claude(payload: list[dict[str, Any]]) -> str:
    """Send payload to Claude on Vertex AI and return the markdown report.

    Uses the Anthropic SDK's Vertex adapter — inference runs inside GCP,
    so PHI (if it ever leaks into the payload despite the repo guard)
    stays under the GCP BAA.
    """
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
        system=SYSTEM_PROMPT,
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


def _write_report(report: str, gcs_bucket: str | None) -> str:
    """Write report to GCS if configured, else print to stdout. Returns a URI."""
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    filename = f"hipaa-log-review/{ts}.md"

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
    logger.error(
        "alert_type=hipaa_review_high report=%s — review findings",
        report_path,
        extra={"alert_type": "hipaa_review_high", "report": report_path},
    )
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
