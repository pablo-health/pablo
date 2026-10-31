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
| **Application audit log** | `AuditService` (`backend/app/services/audit_service.py`) | Structured records of every PHI-touching API request: who (user_id, email), what (action + resource), when, where (IP, user agent), change diff for mutations. The Postgres `audit_logs` row is the record; each row is also mirrored to Cloud Logging as `pablo.audit_events` (`backend/app/services/audit_cloud_logging.py`) | Rows: 2555 days (7 years), then deleted by `audit_retention_cron` (daily, 02:30 UTC on the VM; nightly on a local install). Mirror: 2255 days in the audit sink bucket (`scripts/setup-hipaa-audit-sink.sh`) |
| **Database-level log** | `pgaudit` on the VM's Postgres | Role and DDL changes by database users other than the app. **Not** a record of PHI access: see below | The VM's Postgres log; not shipped off the VM |

The application layer is the audit trail. The hosted service's and the single-VM install's sink configuration differ only in who runs the script; both feed the `<project>-hipaa-audit-6y` bucket.

## Integrity (append-only)

The `audit_logs` table is **append-only** — the application role may `INSERT` and `SELECT` but cannot alter or destroy existing records. This is enforced in the database so a compromised application process can't rewrite history:

- **Self-host (trigger-based).** Every tenant schema carries a `BEFORE UPDATE OR DELETE` trigger (`audit_logs_append_only`) and a statement-level `BEFORE TRUNCATE` trigger (`audit_logs_no_truncate`) that raise on any attempt to edit, delete, or wipe audit rows. They fire even for the table owner — the app connects as that owner, so table privileges alone wouldn't stop it. A row-level UPDATE/DELETE trigger does **not** fire on `TRUNCATE`, which is why the separate TRUNCATE trigger exists (otherwise the trail could be wiped wholesale in one statement). The retention job is the sole exception: it arms a transaction-scoped `SET LOCAL app.allow_audit_purge = 'on'` before deleting rows past the 6-year window. Both triggers ship in `tenant_template.sql` (migrations `b33a493310b6`, `a7e3f1b9c204`).
- **Managed build (privilege-based).** The managed deployment goes further: the application role is a non-owner with `UPDATE`, `DELETE`, **and `TRUNCATE` revoked** on `audit_logs` (role separation), and a separate maintenance role runs retention — so immutability holds by privilege, independent of any trigger.

The weekly pentest of Pablo Health's hosted service verifies this on every run: it asserts the application role cannot effectively UPDATE, DELETE, or TRUNCATE `audit_logs` (privilege revoked, or a BEFORE trigger enforcing it). A self-hosted VM is not pentested by that job.

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

## Database-level log (pgaudit)

On the single-VM install `deploy/vm/bootstrap.sh` loads `pgaudit` and configures it as:

```
pgaudit.log = 'role, ddl'
pgaudit.log_relation = off
pgaudit.log_statement_once = on
ALTER ROLE pablo SET pgaudit.log = 'none';
```

So it records role and schema changes made by other database users (an operator in `psql`, for example) and nothing the application's own role does: relation logging re-logged the whole tenant template on every provisioning. Its lines go to the VM's Postgres log under `/var/log/postgresql/` and are not shipped to Cloud Logging by the installer. `read` and `write` logging are off; the application audit log is the record of data access. Turn `write` on for the `pablo` role only during a forensic investigation, and off again afterwards.

## Querying

### Cloud Logging (recent entries)

```bash
# Last hour of PHI access by a specific user, from the Cloud Logging mirror
gcloud logging read \
  'logName="projects/<project>/logs/pablo.audit_events" AND jsonPayload.user_id="u_123" AND jsonPayload.resource_type="patient"' \
  --freshness=1h --limit=50
```

On the VM, the `audit_logs` table itself is the record: `sudo -u postgres psql pablo` and query `<practice schema>.audit_logs`.

### The six-year bucket (long-range)

The sink copies each log entry as JSON into `gs://<project>-hipaa-audit-6y/`. For a range older than Cloud Logging keeps, download the objects you need with `gsutil cp` and query them locally, or load them into BigQuery yourself; the installer configures no BigQuery dataset.

## Incident investigation

Starting point for a suspected unauthorized access incident:

1. Identify the user(s) and time window from the initial report.
2. Pull all `AuditLogEntry` lines for that user in a wider window (±24h).
3. Cross-reference with Google's admin activity log (SSH through IAP, secret access) for the same window: direct access to the VM or the database bypasses the application audit log, and that log is where it would show.
4. Preserve the logs (Cloud Logging → export to a hold bucket) before rotation.
5. Follow the § 164.402 four-factor breach analysis and § 164.410 notification timelines if ePHI was accessed.

## What this document does **not** cover

- **Failed sign-ins and lockouts.** Successful sign-ins **are** in `audit_logs`: the backend writes one `signed_in` row per sign-in on its first authenticated request (`backend/app/auth/sign_in_audit.py`; MFA enrollment during onboarding is recorded as `onboarding_mfa_enrolled`). Failed password attempts never reach the backend; Identity Platform applies its lockout and keeps its own logs.
- **Infrastructure audit** — Google's Admin Activity log is always on; it captures project-level changes (IAM, VM, Secret Manager access, SSH through IAP). The audit sink keeps it six years.

## Common mistakes

- **Don't** log patient identifiers in `logger.info` — that's not an audit trail, and PHI in stdout outside `AuditService` violates the "No PHI in logs" principle. Use `AuditService` exclusively for PHI-associated events.
- **Don't** rely on `pgaudit` to reconstruct who-saw-what; it is off for the application's role. The `audit_logs` table is the record.
- **Don't** swallow `AuditService.log()` exceptions — a failure to audit is a compliance event, not a soft error.
