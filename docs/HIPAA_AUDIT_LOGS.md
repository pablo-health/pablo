# HIPAA Audit Logs

This document describes Pablo's audit-logging architecture and the controls it satisfies under the HIPAA Security Rule.

## HIPAA requirement

**§ 164.312(b) — Audit Controls.** Covered entities must "implement hardware, software, and/or procedural mechanisms that record and examine activity in information systems that contain or use electronic protected health information (ePHI)."

Operational expectations:

- Track access to ePHI — who, what, when, from where.
- Retain audit logs for at least 6 years (§ 164.316(b)(2)(i)).
- Protect log integrity — append-only, tamper-evident.
- Review logs periodically for anomalies.

## Architecture

Pablo uses a two-layer audit trail:

| Layer | Source | What it captures | Retention |
|---|---|---|---|
| **Application audit log** | `AuditService` (`backend/app/services/audit_service.py`) | Structured records of every PHI-touching API request: who (user_id, email), what (action + resource), when, where (IP, user agent), change diff for mutations | 6 years (log sink) |
| **Database audit log** | Cloud SQL `pgaudit` extension | Raw SQL statements against `patients`, `sessions`, `soap_notes`, etc. — defense-in-depth in case the application layer is bypassed | 6 years (log sink) |

Both layers ship to **Cloud Logging** as structured JSON and are exported to a dedicated Log Sink (Cloud Storage or BigQuery) for long-term retention.

## Application audit log

`AuditService.log()` is invoked from every route that reads or modifies patient data. The emitted log line is a JSON object shaped by `AuditLogEntry` (`backend/app/models/audit.py`), e.g.:

```json
{
  "severity": "INFO",
  "message": "audit: PATIENT_VIEWED by user u_123 on patient/p_456",
  "user_id": "u_123",
  "user_email": "therapist@example.com",
  "action": "PATIENT_VIEWED",
  "resource_type": "patient",
  "resource_id": "p_456",
  "patient_id": "p_456",
  "ip_address": "203.0.113.42",
  "user_agent": "Mozilla/5.0 ...",
  "changes": null,
  "timestamp": "2026-04-16T19:12:03Z"
}
```

**Invariant:** routes that depend on `require_baa_acceptance` **must** take `audit: AuditService = Depends(get_audit_service)` and call `audit.log_*` before returning. See the `CLAUDE.md` guardrail for the rule.

### Covered actions

See `AuditAction` in `backend/app/models/audit.py`. Current coverage includes PATIENT_VIEWED / CREATED / UPDATED / DELETED / LISTED / EXPORTED, SESSION_VIEWED / CREATED / FINALIZED / RATED, SOAP_NOTE_VIEWED / GENERATED / EDITED, APPOINTMENT_SCHEDULED / CANCELLED, and admin actions (USER_ROLE_CHANGED, MFA_ENROLLED, BAA_ACCEPTED).

## Database audit log (pgaudit)

Cloud SQL for PostgreSQL supports the `pgaudit` extension, enabled via database flags. Pablo's setup enables pgaudit at the `write, ddl` level by default:

```
pgaudit.log=write,ddl
pgaudit.log_relation=on
```

This produces a `AUDIT:` prefix on INSERT/UPDATE/DELETE/DDL statements in `postgres.log`, which Cloud SQL forwards to Cloud Logging automatically. `read` logging is **intentionally disabled** by default — the application-level audit already records intent at the API layer, and pgaudit `read` logging is cost- and noise-prohibitive at query volume. Enable it case-by-case for forensic investigations.

## Querying

### Cloud Logging (recent entries)

```bash
# Last hour of PHI access by a specific user
gcloud logging read \
  'jsonPayload.user_id="u_123" AND jsonPayload.resource_type="patient"' \
  --freshness=1h --limit=50

# pgaudit writes to patient rows in the last 24h
gcloud logging read \
  'resource.type="cloudsql_database" AND textPayload:"AUDIT:" AND textPayload:"patients"' \
  --freshness=24h --limit=50
```

### BigQuery (long-range)

If the Log Sink is configured to BigQuery (`audit_logs_bq` dataset), use standard SQL:

```sql
SELECT timestamp, jsonPayload.user_id, jsonPayload.action, jsonPayload.resource_id
FROM `project.audit_logs_bq.cloudaudit_googleapis_com_activity_*`
WHERE jsonPayload.resource_type = 'patient'
  AND jsonPayload.action IN ('PATIENT_EXPORTED', 'PATIENT_DELETED')
  AND _TABLE_SUFFIX BETWEEN FORMAT_DATE('%Y%m%d', DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY))
                        AND FORMAT_DATE('%Y%m%d', CURRENT_DATE())
ORDER BY timestamp DESC;
```

## Incident investigation

Starting point for a suspected unauthorized access incident:

1. Identify the user(s) and time window from the initial report.
2. Pull all `AuditLogEntry` lines for that user in a wider window (±24h).
3. Cross-reference with pgaudit for the same window — any DB access by that user's session that does **not** have a matching application audit entry is a red flag (direct DB access, or an unaudited code path).
4. Preserve the logs (Cloud Logging → export to a hold bucket) before rotation.
5. Follow the § 164.402 four-factor breach analysis and § 164.410 notification timelines if ePHI was accessed.

## What this document does **not** cover

- **Authentication events** (login success/failure, MFA enrollment, token refresh) — these are captured by Firebase Authentication audit logs and should be read alongside the logs above.
- **Infrastructure audit** — GCP Admin Activity logs are always-on and free; they capture project-level changes (IAM, SQL instance config). No Pablo-side configuration is required.

## Common mistakes

- **Don't** log patient identifiers in `logger.info` — that's not an audit trail, and PHI in stdout outside `AuditService` violates the "No PHI in logs" principle. Use `AuditService` exclusively for PHI-associated events.
- **Don't** rely on `pgaudit.log=read` to reconstruct who-saw-what; enable it only during forensic work and disable again when done.
- **Don't** swallow `AuditService.log()` exceptions — a failure to audit is a compliance event, not a soft error.
