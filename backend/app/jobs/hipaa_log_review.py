# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HIPAA § 164.308(a)(1)(ii)(D) — scheduled audit log review.

Runs daily (Cloud Scheduler cron) inside a Cloud Run Job. Queries the
last N hours of `audit_logs`, asks Gemini (via Vertex AI — GCP BAA
covers it) to narrate anomalies, writes the timestamped report to a
retention-locked GCS bucket.

On a HIGH-severity finding the job emits a structured ``ERROR``-level
log entry that an operator-configured Cloud Monitoring log-based alert
policy can use to send email (see ``scripts/monitoring/setup.sh``).

The payload sent to the model is PHI-free by contract (enforced by
AuditRepository.metadata_for_review()). Don't loosen that without
re-evaluating the BAA posture.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("hipaa_log_review")

SYSTEM_PROMPT = """\
You are a HIPAA compliance analyst reviewing 24 hours of audit log
metadata from a therapy documentation platform. The input is PHI-free:
opaque UUIDs, timestamps, action strings, IPs, user agents, and
field-name diffs.

SCOPE — read this first. This input is the audit log of a SINGLE tenant
for a single time window. Anomaly detection has ALREADY been performed
deterministically before you: each row carries the boolean flags below,
the ``user_aggregates`` list holds the per-user alerts, and you are only
invoked at all when at least one flag or aggregate fired. Your job is to
SUMMARISE and PRIORITISE those pre-computed signals into a readable
report and judge their severity — NOT to hunt for anomalies in raw rows.

Two hard rules:
- Report ONLY what the provided fields support. NEVER claim to have run a
  check you cannot run from this input. In particular, do NOT state that
  cross-tenant access was or was not observed: you see one tenant's logs,
  so you cannot judge it — the deterministic ``foreign_actor`` aggregate
  is the only cross-tenant signal, and its absence is not something for
  you to assert. Presenting an impossible check as "passed" is a
  false-assurance defect, the worst kind of line in a compliance report.
- The ``entries`` list may be a CAPPED subset (flagged rows plus a recent
  sample); the ``summary`` block carries the true totals. Reason about
  volume from ``summary``, and never describe omitted rows as reviewed.

Every ``entries`` row and every ``user_aggregates`` item also carries an
``is_internal_actor`` boolean. When true, the user is an authorized
automated actor the operator has registered (a scheduled internal scan,
a test/E2E identity, a health-check job) — NOT an unknown account.
Machine-paced behaviour from these actors (rapid sequential reads,
datacenter-IP origin, high request volume, off-hours activity) is
expected by design. ATTRIBUTE such activity to the known automated actor
and report it as informational/LOW; do not escalate it to HIGH purely
for looking automated or scraper-like. A genuine red flag from an
internal actor — e.g. a bulk delete, an export spike far above its own
baseline, or access to a record outside its documented scope — is still
worth flagging at the severity the evidence warrants; ``is_internal_actor``
explains the traffic shape, it does not waive scrutiny of the action.

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
   - ``is_unauthorized_access``: true when a chart-read action has NO
     live clinician grant behind it (the user holds no current
     ``patient_clinicians`` row for that patient). Approximate
     current-state — a grant revoked since the access reads as
     unauthorized — so treat it as a signal to surface, not proof.
     Strong when combined with novelty or no-relationship.
   - ``is_off_hours``: true when the access fell in the platform's
     deep-night UTC band. Context, not an alarm on its own.

2. ``user_aggregates`` — per-user alerts that don't fit the per-row
   shape. Each has an ``alert`` type:
   - ``bulk_delete``: user deleted > 3 patients in the window.
   - ``high_export_rate``: user's export count today exceeds their
     own 90-day P95 baseline (only fires with >= 14 days of baseline).
   - ``foreign_actor``: a user_id acting in this tenant that is NOT in
     the tenant's authorized roster — a user operating in a practice
     that does not employ them. This is the deterministic cross-tenant
     breach signal and is computed in code; when present, treat it as
     HIGH unless an aggregate is also flagged ``is_internal_actor``.

Warmup note: a brand-new install, a first-week user, or a user
returning after > 7 days away will have most flags FALSE. That means
"we don't have enough history to judge," NOT "all clear." If most
rows are flag-less, say so in the report and focus on the other
anomaly types below.

Narrate and prioritise the signals already surfaced. Weigh:
- Rows where any of ``is_novel_user_patient``, ``is_same_last_name``,
  ``is_no_treatment_relationship``, or ``is_unauthorized_access`` are
  true — strongest when several stack on the same access.
- Any ``user_aggregates`` alerts (bulk_delete, high_export_rate,
  foreign_actor).
- ``is_off_hours`` rows and unusual action sequences (e.g. a
  LIST -> VIEW -> EXPORT chain) visible among the entries — these add
  context to a flagged access; on their own they are informational.

Output a markdown report with sections:
## Summary (one paragraph)
## Anomalies (numbered, each with: severity HIGH/MEDIUM/LOW, evidence, recommended action)
## No-op confirmations (things you checked and found nothing unusual)

End the report with exactly one line in this form — the highest
severity among the anomalies you listed, or NONE when there are none:
Overall severity: HIGH|MEDIUM|LOW|NONE

Reference users/patients by UUID only; never invent identifying
information. If the input is empty or too sparse, say so and stop.
"""


MONTHLY_WINDOW_HOURS = 24 * 30


def run(
    window_hours: int = 24,
    gcs_bucket: str | None = None,
) -> int:
    """Entry point. Returns 0 on success, non-zero if any tenant failed.

    REVIEW_WINDOW_HOURS=720 switches to the monthly rollup review.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    gcs_bucket = gcs_bucket or os.environ.get("COMPLIANCE_REPORT_BUCKET")
    window_hours = int(os.environ.get("REVIEW_WINDOW_HOURS", window_hours))

    review_mode = "monthly" if window_hours >= MONTHLY_WINDOW_HOURS else "daily"

    invariant_violations = _assert_schema_flag_consistency()
    if invariant_violations:
        logger.error("Schema/flag invariant violations detected: %s", invariant_violations)
        _notify_invariant_violations(invariant_violations, gcs_bucket=gcs_bucket)

    schemas = _list_practice_schemas(include_pentest=False)
    logger.info(
        "Starting HIPAA log review — window_hours=%d mode=%s tenants=%d",
        window_hours,
        review_mode,
        len(schemas),
    )

    exit_code = 0
    for schema in schemas:
        try:
            _review_tenant(
                practice_schema=schema,
                window_hours=window_hours,
                review_mode=review_mode,
                gcs_bucket=gcs_bucket,
            )
        except Exception:
            logger.exception("Review failed for tenant schema=%s", schema)
            exit_code = 1

    return exit_code


def _list_practice_schemas(*, include_pentest: bool = False) -> list[str]:
    # LEFT JOIN so the single-tenant 'practice' schema (no registry row)
    # isn't dropped.
    from sqlalchemy import text  # noqa: PLC0415

    from ..db import get_engine  # noqa: PLC0415

    sql = (
        "SELECT s.schema_name "
        "FROM information_schema.schemata AS s "
        "LEFT JOIN platform.practices AS p "
        "  ON p.schema_name = s.schema_name "
        "WHERE s.schema_name = 'practice' "
        "   OR s.schema_name LIKE 'practice_%' "
    )
    if not include_pentest:
        sql += "AND (p.is_pentest IS NULL OR p.is_pentest = FALSE) "
    sql += "ORDER BY s.schema_name"

    with get_engine().connect() as conn:
        rows = conn.execute(text(sql)).fetchall()
    return [r[0] for r in rows]


def _assert_schema_flag_consistency() -> list[str]:
    # Re-verify invariants the CHECK/trigger enforce at write time;
    # superuser UPDATE or manual SQL could bypass them.
    from sqlalchemy import text  # noqa: PLC0415

    from ..db import get_engine  # noqa: PLC0415

    violations: list[str] = []
    with get_engine().connect() as conn:
        mismatched_schemas = conn.execute(
            text(
                r"SELECT s.schema_name"
                r" FROM information_schema.schemata s"
                r" LEFT JOIN platform.practices p"
                r"   ON p.schema_name = s.schema_name"
                r" WHERE s.schema_name LIKE 'practice\_pentest\_%' ESCAPE '\'"
                r"   AND (p.is_pentest IS NULL OR p.is_pentest = FALSE)"
            )
        ).fetchall()
        for (schema,) in mismatched_schemas:
            violations.append(f"schema={schema} matches pentest pattern but is_pentest is not TRUE")

        mismatched_flags = conn.execute(
            text(
                r"SELECT schema_name"
                r" FROM platform.practices"
                r" WHERE is_pentest = TRUE"
                r"   AND schema_name NOT LIKE 'practice\_pentest\_%' ESCAPE '\'"
            )
        ).fetchall()
        for (schema,) in mismatched_flags:
            violations.append(
                f"schema={schema} has is_pentest=TRUE but does not match pentest pattern"
            )
    return violations


def _notify_invariant_violations(violations: list[str], gcs_bucket: str | None) -> None:
    body = "# HIPAA Log Review — Platform Invariant Violations\n\n"
    body += "**Severity: HIGH**\n\n"
    body += (
        "Schema-name / ``is_pentest`` flag divergence detected. DB "
        "CHECK + trigger should make this impossible; if you see this "
        "report, something bypassed them (superuser UPDATE or direct SQL). "
        "Investigate immediately.\n\n"
    )
    body += "## Violations\n\n"
    for v in violations:
        body += f"- {v}\n"
    report_path = _write_report(body, gcs_bucket, review_mode="invariants", tenant_schema=None)
    _notify_high_finding(report_path, tenant_schema=None)


def _review_tenant(
    practice_schema: str,
    window_hours: int,
    review_mode: str,
    gcs_bucket: str | None,
) -> None:
    payload = _load_review_payload(practice_schema, window_hours)
    summary = payload["summary"]
    logger.info(
        "schema=%s total_entries=%d sent=%d aggregates=%d needs_model=%s",
        practice_schema,
        summary["total_entries"],
        summary["entries_sent"],
        len(payload["user_aggregates"]),
        summary["needs_model_review"],
    )

    # Deterministic triage: when the in-code pass classified every row as
    # provably routine and raised no aggregates, the window is "fine" and
    # never reaches the model — cheaper, and the report states only what
    # code verified (no model, no false assurance). The model is invoked
    # only when there is a genuine anomaly cluster worth narrating.
    if not summary["needs_model_review"]:
        report = _deterministic_clean_report(summary, review_mode)
        severity = "NONE"
    else:
        report = _ask_model(payload, review_mode=review_mode, tenant_schema=practice_schema)
        severity = _parse_severity(report)

    report_path = _write_report(
        report, gcs_bucket, review_mode=review_mode, tenant_schema=practice_schema
    )
    logger.info("schema=%s report=%s severity=%s", practice_schema, report_path, severity)

    if severity == "HIGH":
        _notify_high_finding(report_path, tenant_schema=practice_schema)


def _deterministic_clean_report(summary: dict[str, Any], review_mode: str) -> str:
    """Report for a window the deterministic pass classified as routine — no
    model call. States only what code actually verified, so it is honest by
    construction: it never claims a check it did not run (e.g. it does not
    assert anything about cross-tenant access beyond the foreign-actor scan,
    nor about off-hours), which is the false-assurance failure mode this
    whole path exists to avoid."""
    total = summary["total_entries"]
    window = "30-day" if review_mode == "monthly" else "24-hour"
    if total == 0:
        return (
            f"# HIPAA Log Review\n\nNo audit activity in the {window} window.\n\n"
            "Overall severity: NONE\n"
        )
    return (
        "# HIPAA Log Review\n\n"
        "## Summary\n"
        f"Reviewed {total} audit entries ({summary['distinct_users']} users, "
        f"{summary['distinct_patients']} patients) over the {window} window. "
        "Every chart access was grant-backed and by a user belonging to this "
        "tenant; no exports or deletions, no bulk-delete or export-rate "
        "alerts, and no foreign-actor (cross-tenant) access. Classified "
        "routine by the deterministic pass; no model review was required.\n\n"
        "## Anomalies\nNone.\n\n"
        "## No-op confirmations\n"
        "- All accesses backed by a live clinician grant.\n"
        "- No user_id outside this tenant's authorized roster.\n"
        "- No export, delete, bulk-delete, or export-rate-spike activity.\n\n"
        "Overall severity: NONE\n"
    )


def _load_review_payload(practice_schema: str, window_hours: int) -> dict[str, Any]:
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
    from ..settings import get_settings  # noqa: PLC0415

    session = create_standalone_session(practice_schema=practice_schema)
    try:
        service = AuditReviewService(
            audit_repo=PostgresAuditRepository(session),
            patient_repo=PostgresPatientRepository(session),
            user_repo=PostgresUserRepository(session),
            appointment_repo=PostgresAppointmentRepository(session),
            session_repo=PostgresTherapySessionRepository(session),
        )
        payload = service.compute_payload(
            window_hours=window_hours,
            internal_actor_user_ids=get_settings().internal_actor_user_ids,
            authorized_user_ids=_authorized_user_ids(session, practice_schema),
        )
        return payload.to_dict()
    finally:
        session.close()


def _authorized_user_ids(session: Any, practice_schema: str) -> set[str] | None:
    """The set of user_ids that legitimately belong to ``practice_schema``:
    its members (per ``platform.email_tenant_mappings``), its practice owner,
    and all platform admins. Drives the reviewer's deterministic cross-tenant
    (foreign-actor) check.

    Returns ``None`` when the schema has no platform tenant_id — a
    self-hosted single-tenant install, where "foreign actor" is undefined
    and the check must be skipped, not guessed. Email match is
    case-insensitive (the mapping stores the login email; users store theirs).
    """
    from sqlalchemy import text  # noqa: PLC0415

    tenant_id = session.execute(
        text("SELECT tenant_id FROM platform.practices WHERE schema_name = :s"),
        {"s": practice_schema},
    ).scalar()
    if not tenant_id:
        return None

    rows = session.execute(
        text(
            "SELECT u.id FROM platform.users u "
            "JOIN platform.email_tenant_mappings m "
            "  ON lower(u.email) = lower(m.email) "
            "WHERE m.tenant_id = :tid "
            "UNION "
            "SELECT u.id FROM platform.users u "
            "JOIN platform.practices p ON lower(p.owner_email) = lower(u.email) "
            "WHERE p.tenant_id = :tid "
            "UNION "
            "SELECT id FROM platform.users WHERE is_platform_admin = TRUE"
        ),
        {"tid": tenant_id},
    ).fetchall()
    return {str(r[0]) for r in rows}


def _ask_model(
    payload: dict[str, Any],
    review_mode: str = "daily",
    tenant_schema: str | None = None,
) -> str:
    """Send payload to Gemini on Vertex AI and return the markdown report.

    Uses the same ``google.genai`` Vertex client as the rest of the
    platform — inference runs inside GCP, so PHI (if it ever leaks into
    the payload despite the repo guard) stays under the GCP BAA.
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
        from google import genai  # noqa: PLC0415
        from google.genai import types  # noqa: PLC0415
    except ImportError:
        logger.exception("google-genai is required for the HIPAA log review.")
        raise

    from ..settings import get_settings  # noqa: PLC0415

    project = os.environ.get("GCP_PROJECT_ID") or os.environ["GOOGLE_CLOUD_PROJECT"]
    # Gemini 3.x is served from the ``global`` Vertex location, not a single
    # region; the cron env may not carry the backend service's
    # GOOGLE_CLOUD_LOCATION, so default it here.
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
    model = os.environ.get("HIPAA_REVIEW_MODEL") or get_settings().ai_model
    # Thinking models spend part of the output budget on reasoning before
    # emitting the report; size generously so the narrative isn't truncated.
    max_output_tokens = int(os.environ.get("HIPAA_REVIEW_MAX_OUTPUT_TOKENS", "8192"))

    client = genai.Client(vertexai=True, project=project, location=location)
    user_content = (
        f"Review the following audit log entries:\n\n```json\n{json.dumps(payload, indent=2)}\n```"
    )
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=0.3,
        max_output_tokens=max_output_tokens,
    )
    resp = client.models.generate_content(
        model=model,
        contents=user_content,
        config=config,
    )
    _log_token_usage(resp, review_mode=review_mode, tenant_schema=tenant_schema)
    report = (resp.text or "").strip()
    if not report:
        # An empty response must fail the tenant's review loudly (exit 1,
        # exception in the log) — falling back to a placeholder would
        # parse as severity NONE and a transient model failure would
        # masquerade as a clean day.
        msg = "Model returned an empty report — failing this review rather than recording NONE"
        raise RuntimeError(msg)
    return report


def _log_token_usage(resp: Any, *, review_mode: str, tenant_schema: str | None) -> None:
    """Emit the model's token counts at INFO so each run's cost is visible
    in the job logs. Counts only — no prompt or response content — so this
    stays PHI-free. The SDK leaves ``usage_metadata`` (or any individual
    field) unset on some responses, so every read is guarded.
    """
    usage = getattr(resp, "usage_metadata", None)
    if usage is None:
        return
    logger.info(
        "token_usage schema=%s mode=%s prompt=%s candidates=%s thoughts=%s total=%s",
        tenant_schema or "-",
        review_mode,
        getattr(usage, "prompt_token_count", None),
        getattr(usage, "candidates_token_count", None),
        getattr(usage, "thoughts_token_count", None),
        getattr(usage, "total_token_count", None),
    )


# The structured line the SYSTEM_PROMPT asks the model to end with.
# Tolerates markdown bold/blockquote decoration around the line.
_OVERALL_SEVERITY_RE = re.compile(
    r"(?im)^[ \t>*_-]*(?:\*\*)?\s*overall severity(?:\*\*)?\s*[:=]"
    r"\s*(?:\*\*)?\s*(HIGH|MEDIUM|LOW|NONE)\b"
)

# Body of the "## Anomalies" section: everything up to the next heading.
_ANOMALIES_SECTION_RE = re.compile(r"(?ims)^#{1,6}[ \t]*anomalies\b[^\n]*$(.*?)(?=^#{1,6}[ \t]|\Z)")


def _parse_severity(report: str) -> str:
    """Extract the overall severity from the markdown report.

    Primary contract: the prompt-mandated ``Overall severity: <TOKEN>``
    line (last occurrence wins). Fallback for non-compliant reports:
    per-anomaly severity markers inside the ``## Anomalies`` section
    only. A bare substring match is wrong here — prose like "no
    HIGH-severity findings were identified" must not page anyone.
    """
    line_matches: list[str] = _OVERALL_SEVERITY_RE.findall(report)
    if line_matches:
        return line_matches[-1].upper()

    section = _ANOMALIES_SECTION_RE.search(report)
    scope = section.group(1) if section else report
    for token in ("HIGH", "MEDIUM", "LOW"):
        if _has_severity_marker(scope, token):
            return token
    return "NONE"


def _has_severity_marker(text: str, token: str) -> bool:
    """True when ``token`` appears as a severity *marker*, not prose.

    Accepted shapes: ``severity: HIGH`` (any spacing/bold), ``[HIGH]``,
    ``**HIGH**``, or a list/numbered item starting with the token
    (``1. HIGH — …``). Negated prose ("No HIGH-severity findings")
    matches none of these.
    """
    patterns = (
        rf"(?i)severity\s*(?:\*\*)?\s*[:\-—]?\s*(?:\*\*)?\s*{token}\b",
        rf"(?i)\[\s*{token}\s*\]",
        rf"(?i)\*\*\s*{token}\s*\*\*",
        rf"(?im)^\s*(?:\d+[.)]|[-*•])\s*\**\s*{token}\b",
    )
    return any(re.search(p, text) for p in patterns)


def _write_report(
    report: str,
    gcs_bucket: str | None,
    review_mode: str = "daily",
    tenant_schema: str | None = None,
) -> str:
    """Write report to GCS if configured, else stdout. Returns the URI."""
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    if tenant_schema:
        filename = f"hipaa-log-review/{tenant_schema}/{review_mode}/{ts}.md"
    else:
        filename = f"hipaa-log-review/{review_mode}/{ts}.md"

    if not gcs_bucket:
        logger.warning("COMPLIANCE_REPORT_BUCKET unset — printing report to stdout")
        sys.stdout.write(report + "\n")
        return f"stdout://{filename}"

    try:
        from google.cloud import storage  # type: ignore[attr-defined]  # noqa: PLC0415
    except ImportError:
        logger.exception("google-cloud-storage not installed; add to pyproject main deps.")
        raise

    client = storage.Client()
    blob = client.bucket(gcs_bucket).blob(filename)
    blob.upload_from_string(report, content_type="text/markdown")
    return f"gs://{gcs_bucket}/{filename}"


def _notify_high_finding(report_path: str, tenant_schema: str | None = None) -> None:
    """Emit structured ERROR log for Cloud Monitoring + optional webhook POST."""
    schema_fragment = f" schema={tenant_schema}" if tenant_schema else ""
    payload: dict[str, str] = {
        "severity": "ERROR",
        "message": (
            f"alert_type=hipaa_review_high{schema_fragment} report={report_path} — review findings"
        ),
        "alert_type": "hipaa_review_high",
        "report": report_path,
    }
    if tenant_schema:
        payload["tenant_schema"] = tenant_schema
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()
    webhook = os.environ.get("ALERT_WEBHOOK_URL")
    if webhook:
        _post_webhook(
            webhook,
            report_path,
            alert_source="hipaa-log-review",
            tenant_schema=tenant_schema,
        )


HTTP_REDIRECT_THRESHOLD = 300


def _post_webhook(
    webhook_url: str,
    report_path: str,
    alert_source: str,
    tenant_schema: str | None = None,
) -> None:
    import urllib.request  # noqa: PLC0415

    schema_fragment = f" ({tenant_schema})" if tenant_schema else ""
    body_dict: dict[str, str] = {
        "source": alert_source,
        "severity": "HIGH",
        "text": (f"{alert_source}: HIGH severity finding{schema_fragment} — {report_path}"),
        "report": report_path,
    }
    if tenant_schema:
        body_dict["tenant_schema"] = tenant_schema
    body = json.dumps(body_dict).encode()
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
