---
name: phi-log-scan
description: Find logger / print / structlog calls in backend/app/ that reference PHI field names. Use when the user says "run /phi-log-scan", worries a new log line might leak PHI, or is reviewing a CI failure about PHI in stdout. Intentional AuditService calls are excluded.
tools: [Read, Bash, Glob]
---

# PHI Log Scan

Enforces CLAUDE.md guardrail #5: "PHI never enters stdout."

Walks every `.py` file under `backend/app/` and, for each call
expression, checks whether its printable arguments (f-strings, format
strings, `.format(...)`, `%`-style) reference PHI field names. Calls on
`AuditService` (or any `audit.*`) are intentional and skipped — those
are the sanctioned PHI path.

## How to run

```bash
python .claude/skills/phi-log-scan/check.py
```

## PHI field names

`patient_name`, `first_name`, `last_name`, `email`, `phone`, `dob`,
`diagnosis`, `ssn`, `mrn`, `transcript`, `note_body`, `address`.

## What counts as a "log call"

Any call whose invocation name matches:

- `logger.<level>`, `log.<level>`, `logging.<level>`
- `structlog.*`
- bare `print(...)`

`<level>` is any of: `debug`, `info`, `warning`, `warn`, `error`,
`critical`, `exception`.

## What is intentionally skipped

- Calls on anything named `audit` (`audit.log_event(...)`,
  `self._audit.log_*(...)`)
- Calls inside `backend/tests/` and `backend/app/services/audit_service.py`
  (the audit machinery itself)

## Output

For each finding: `file:line  call  suggested fix` — typical fix is to
drop the PHI token and reference the resource by ID instead, e.g.:

```python
logger.info("patient_name=%s", patient.name)
# →
logger.info("patient_id=%s", patient.id)
```

Exit code 1 if any findings, 0 otherwise.
