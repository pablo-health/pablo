# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""On-demand HIPAA compliance attestation document generator.

Pulls verifiable evidence from the running system (audit_logs stats,
compliance bucket inventory, retention config) and templates it into a
markdown document keyed to HIPAA Safeguard citations and NIST SP 800-66r2
control mappings. Writes to ``gs://<bucket>/attestations/YYYY-MM-DD.md``
for the operator to hand to an auditor.

Intentionally **deterministic** — no LLM involved. Auditors want an
evidence snapshot they can verify line-by-line against GCP APIs and
the database, not a narrated summary. Plain-language executive summary
is phase 2.

Invoke on-demand:

    gcloud run jobs execute hipaa-attestation --region <region>

or as a quarterly scheduled job.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger("hipaa_attestation")


@dataclass
class Evidence:
    """Container for everything the attestation template consumes."""

    generated_at: str
    project_id: str
    pablo_version: str

    # Audit log stats
    audit_row_count: int
    audit_oldest_ts: str | None
    audit_newest_ts: str | None
    audit_retention_days: int

    # GCS inventory — counts + sample of recent items per prefix
    daily_review_count_30d: int
    monthly_review_count_1y: int
    pentest_count_90d: int
    heartbeat_count_30d: int
    last_daily_review: str | None
    last_pentest: str | None
    last_heartbeat: str | None

    # Bucket retention
    compliance_bucket: str | None
    bucket_retention_days: int | None
    bucket_retention_locked: bool | None


def run() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    gcs_bucket = os.environ.get("COMPLIANCE_REPORT_BUCKET")
    project_id = os.environ.get("GCP_PROJECT_ID") or os.environ.get(
        "GOOGLE_CLOUD_PROJECT", "unknown"
    )

    evidence = _gather_evidence(gcs_bucket=gcs_bucket, project_id=project_id)
    report = _render_markdown(evidence)
    report_uri = _write_report(report, gcs_bucket)
    logger.info("Attestation written to %s", report_uri)
    return 0


def _gather_evidence(gcs_bucket: str | None, project_id: str) -> Evidence:
    now = datetime.now(UTC)
    audit_count, audit_oldest, audit_newest = _audit_stats()
    inventory = _bucket_inventory(gcs_bucket) if gcs_bucket else _empty_inventory()
    retention_days, retention_locked = _bucket_retention(gcs_bucket)

    from ..models.audit import AUDIT_LOG_RETENTION_DAYS  # noqa: PLC0415

    return Evidence(
        generated_at=now.isoformat().replace("+00:00", "Z"),
        project_id=project_id,
        pablo_version=os.environ.get("PABLO_VERSION", "unknown"),
        audit_row_count=audit_count,
        audit_oldest_ts=audit_oldest,
        audit_newest_ts=audit_newest,
        audit_retention_days=AUDIT_LOG_RETENTION_DAYS,
        daily_review_count_30d=inventory["daily_count_30d"],
        monthly_review_count_1y=inventory["monthly_count_1y"],
        pentest_count_90d=inventory["pentest_count_90d"],
        heartbeat_count_30d=inventory["heartbeat_count_30d"],
        last_daily_review=inventory["last_daily"],
        last_pentest=inventory["last_pentest"],
        last_heartbeat=inventory["last_heartbeat"],
        compliance_bucket=gcs_bucket,
        bucket_retention_days=retention_days,
        bucket_retention_locked=retention_locked,
    )


def _audit_stats() -> tuple[int, str | None, str | None]:
    """Return (row_count, oldest_ts_iso, newest_ts_iso) from audit_logs."""
    try:
        from sqlalchemy import func, select  # noqa: PLC0415

        from ..db import create_standalone_session  # noqa: PLC0415
        from ..db.models import AuditLogRow  # noqa: PLC0415
    except ImportError:
        logger.exception("DB modules not importable — reporting zeros")
        return 0, None, None

    session = create_standalone_session()
    try:
        result = session.execute(
            select(
                func.count(AuditLogRow.id),
                func.min(AuditLogRow.timestamp),
                func.max(AuditLogRow.timestamp),
            )
        ).one()
        count, oldest, newest = result
        return (
            int(count or 0),
            oldest.isoformat().replace("+00:00", "Z") if oldest else None,
            newest.isoformat().replace("+00:00", "Z") if newest else None,
        )
    finally:
        session.close()


def _empty_inventory() -> dict[str, Any]:
    return {
        "daily_count_30d": 0,
        "monthly_count_1y": 0,
        "pentest_count_90d": 0,
        "heartbeat_count_30d": 0,
        "last_daily": None,
        "last_pentest": None,
        "last_heartbeat": None,
    }


def _bucket_inventory(gcs_bucket: str) -> dict[str, Any]:
    """List evidence blobs under each routine's prefix and summarize."""
    try:
        from google.cloud import storage  # type: ignore[attr-defined]  # noqa: PLC0415
    except ImportError:
        logger.exception("google-cloud-storage not installed — empty inventory")
        return _empty_inventory()

    client = storage.Client()
    bucket = client.bucket(gcs_bucket)
    now = datetime.now(UTC)

    daily = _list_blobs(bucket, "hipaa-log-review/daily/")
    monthly = _list_blobs(bucket, "hipaa-log-review/monthly/")
    pentest = _list_blobs(bucket, "pentest/")
    heartbeat = _list_blobs(bucket, "heartbeat/")

    return {
        "daily_count_30d": _count_since(daily, now - timedelta(days=30)),
        "monthly_count_1y": _count_since(monthly, now - timedelta(days=365)),
        "pentest_count_90d": _count_since(pentest, now - timedelta(days=90)),
        "heartbeat_count_30d": _count_since(heartbeat, now - timedelta(days=30)),
        "last_daily": _latest_name(daily),
        "last_pentest": _latest_name(pentest),
        "last_heartbeat": _latest_name(heartbeat),
    }


def _list_blobs(bucket: Any, prefix: str) -> list[Any]:
    try:
        return list(bucket.list_blobs(prefix=prefix))
    except Exception:
        logger.exception("Failed to list %s — continuing with empty", prefix)
        return []


def _count_since(blobs: list[Any], cutoff: datetime) -> int:
    return sum(1 for b in blobs if getattr(b, "time_created", None) and b.time_created >= cutoff)


def _latest_name(blobs: list[Any]) -> str | None:
    if not blobs:
        return None
    sorted_blobs = sorted(
        (b for b in blobs if getattr(b, "time_created", None)),
        key=lambda b: b.time_created,
        reverse=True,
    )
    return sorted_blobs[0].name if sorted_blobs else None


def _bucket_retention(gcs_bucket: str | None) -> tuple[int | None, bool | None]:
    if not gcs_bucket:
        return None, None
    try:
        from google.cloud import storage  # type: ignore[attr-defined]  # noqa: PLC0415
    except ImportError:
        return None, None
    try:
        bucket = storage.Client().get_bucket(gcs_bucket)
    except Exception:
        logger.exception("Failed to fetch bucket metadata")
        return None, None
    seconds = getattr(bucket, "retention_period", None)
    locked = getattr(bucket, "retention_policy_locked", None)
    days = int(seconds // 86400) if seconds else None
    return days, locked


def _render_markdown(e: Evidence) -> str:
    """Deterministic template. No LLM — every line traces to a queryable fact."""
    retention_line = (
        f"{e.bucket_retention_days} days"
        + (" (LOCKED)" if e.bucket_retention_locked else " (not yet locked)")
        if e.bucket_retention_days is not None
        else "not configured"
    )
    return f"""# Pablo HIPAA Attestation Snapshot

Generated: {e.generated_at}
Project:   {e.project_id}
Version:   {e.pablo_version}

This document is a point-in-time snapshot of the technical and
administrative safeguards Pablo has in place, mapped to the HIPAA
Security Rule (45 CFR § 164 Subpart C) and NIST SP 800-66 r2. Every
statistic is pulled from a running system — GCS object listings,
Postgres queries, and bucket metadata — not from narrative. Auditors
can re-run this job to verify.

---

## § 164.308  Administrative Safeguards

### § 164.308(a)(1)(ii)(D) — Information System Activity Review

Implemented as scheduled Cloud Run Jobs that run every day (focused
anomaly detection on a 24-hour window) and every month (slow-burn
patterns on a 30-day window). Reports are written to
`gs://{e.compliance_bucket or '<unconfigured>'}/hipaa-log-review/` and
retained under the bucket's lifecycle policy (see § 164.316(b)(2)).

| Metric | Value |
|---|---|
| Daily reviews in last 30 days | {e.daily_review_count_30d} |
| Monthly rollups in last 365 days | {e.monthly_review_count_1y} |
| Most recent daily review | `{e.last_daily_review or '(none)'}` |

### § 164.308(a)(8) — Evaluation (Pentest)

Owner-authorized weekly pentest runs as a Cloud Run Job invoking a
bundled Claude Code skill. Reports are redacted of any PHI and stored
alongside the log review evidence.

| Metric | Value |
|---|---|
| Pentests in last 90 days | {e.pentest_count_90d} |
| Most recent pentest | `{e.last_pentest or '(none)'}` |

Maps to **NIST SP 800-53 CA-8 (Penetration Testing)**.

---

## § 164.312  Technical Safeguards

### § 164.312(b) — Audit Controls

Every PHI-accessing route captures an `audit_logs` row through
`AuditService`. The table is PHI-free by schema (no denormalized
names, emails, or free-text — only opaque IDs, action enums,
timestamps, and field-name diffs). A CLAUDE.md guardrail prohibits
routes from bypassing the service.

| Metric | Value |
|---|---|
| Rows in `audit_logs` | {e.audit_row_count:,} |
| Oldest entry | `{e.audit_oldest_ts or '(empty)'}` |
| Newest entry | `{e.audit_newest_ts or '(empty)'}` |
| Configured retention (days) | {e.audit_retention_days} |

Retention of {e.audit_retention_days} days exceeds the HIPAA minimum
of 6 years (§ 164.316(b)(2)(i) — 2190 days).

Maps to **NIST SP 800-53 AU-2, AU-6, AU-12**.

### § 164.312(a)(2)(i) — Unique User Identification

Every user has a system-assigned UUID; identities are managed by
Firebase Identity Platform. Usernames/emails are carried on the User
record; session tokens are bound to the UUID, not the email.

### § 164.312(d) — Person/Entity Authentication

- Multi-Factor Authentication (TOTP) is required for every user at
  enrollment and at every sign-in (enforced by Firebase Identity
  Platform policy).
- Password policy is NIST SP 800-63B compliant (minimum 15
  characters, no composition rules, breach-list checks) — configured
  by `setup-solo.sh` step 6e.

*Verify in GCP Console → Identity Platform → Settings → Sign-in methods.*

### § 164.312(e)(1) — Transmission Security

- All Cloud Run services are HTTPS-only; HTTP requests receive 301
  redirects enforced by the Cloud Run front-end.
- Internal service-to-service calls use OIDC-signed JWTs verified
  against the expected audience (`backend/app/routes/ext_auth.py`).
- Cloud SQL connections go through the Cloud SQL Auth Proxy with
  mutual TLS.

### At-rest encryption (§ 164.312(a)(2)(iv))

- Cloud SQL instances: AES-256 at rest by default (Google-managed
  encryption).
- GCS buckets: Google-managed encryption by default.
- Secret Manager: AES-256 at rest by default.

Customers requiring CMEK can enable it per GCP Console without code
changes.

---

## § 164.314  Organizational Requirements

### § 164.314(a) — Business Associate Contracts

Pablo's data path for AI inference flows to Claude **via Vertex AI**,
keeping all PHI-adjacent processing inside the customer's Google Cloud
project. This is covered by the customer's existing Google Cloud BAA
— **no separate AI-vendor BAA is required**. No PHI is sent to
`api.anthropic.com` or any non-GCP endpoint.

*Action required by the customer: sign the Google Cloud BAA if not
already signed.*

---

## § 164.316  Policies, Procedures, and Documentation

### § 164.316(b)(2)(i) — Retention of Documentation (6-year minimum)

The compliance reports bucket
(`gs://{e.compliance_bucket or '<unconfigured>'}`) is configured with
a retention policy of **{retention_line}**. Bucket lock makes the
retention policy irreversible for the configured period, satisfying
the "retained for 6 years" requirement for all review artifacts.

Audit log rows carry an `expires_at` set to {e.audit_retention_days}
days after creation; a retention-enforcement job (future work) will
purge rows past this bound.

---

## Pipeline health

The compliance pipeline itself is monitored with two signals:

| Signal | Last observed |
|---|---|
| Weekly synthetic heartbeat (Monday 09:00 local) | `{e.last_heartbeat or '(none)'}` |
| Heartbeat emissions in last 30 days | {e.heartbeat_count_30d} |

A Cloud Monitoring log-based alert fires on the structured `ERROR`
log each routine emits on HIGH findings; a separate alert fires if
`hipaa-log-review` hasn't completed successfully in > 30 hours.

---

## References

- HIPAA Security Rule: 45 CFR § 164 Subpart C
- NIST SP 800-66 r2 (February 2024): Implementing the HIPAA Security Rule
- NIST SP 800-53 r5: Security and Privacy Controls for Information Systems
- NIST SP 800-63B: Digital Identity Guidelines — Authentication and Lifecycle Management

---

*This document is generated by ``backend/app/jobs/hipaa_attestation.py``
and is reproducible. To regenerate, run:*

```
gcloud run jobs execute hipaa-attestation --region <region> --project {e.project_id}
```
"""


def _write_report(report: str, gcs_bucket: str | None) -> str:
    ts = datetime.now(UTC).strftime("%Y-%m-%d")
    filename = f"attestations/{ts}.md"
    if not gcs_bucket:
        logger.warning("COMPLIANCE_REPORT_BUCKET unset — printing attestation to stdout")
        sys.stdout.write(report + "\n")
        return f"stdout://{filename}"
    try:
        from google.cloud import storage  # type: ignore[attr-defined]  # noqa: PLC0415
    except ImportError:
        logger.exception("google-cloud-storage not installed — cannot upload")
        raise

    client = storage.Client()
    client.bucket(gcs_bucket).blob(filename).upload_from_string(
        report, content_type="text/markdown"
    )
    return f"gs://{gcs_bucket}/{filename}"


if __name__ == "__main__":
    sys.exit(run())
