---
name: audit-coverage-check
description: Scan route handlers for PHI-touching routes that don't inject or call AuditService. Use when the user asks to audit the HIPAA audit-logging guardrail, says "run /audit-coverage-check", or adds new routes under /patients, /sessions, /appointments, /notes, /transcript, /audio, /soap, /resolve-client, /import-clients.
tools: [Read, Bash, Glob]
---

# Audit Coverage Check

Enforces CLAUDE.md guardrail #1: every PHI-touching route handler must inject
`audit: AuditService = Depends(get_audit_service)` AND call `audit.<helper>(...)`
in its body.

## How to run

```bash
python .claude/skills/audit-coverage-check/check.py
```

## Single source of truth

This skill is a thin wrapper. The actual check lives in
`backend/scripts/check_route_audit.py` — a pure-stdlib AST script with no
app/DB/network dependency — and the **same** implementation backs four
surfaces so they can't drift:

- this on-demand skill (report-shaped, run mid-edit);
- the CI gate `backend/tests/test_route_audit_guardrails.py` (delegates to it);
- the pre-commit gate `scripts/check_audit_params.py` (delegates to it);
- the `PostToolUse` hook in `.claude/settings.json`, which runs it on every
  edit to a route file and feeds violations straight back to the agent.

The engine auto-detects route roots (`backend/app/routes/` in the OSS engine; a
downstream deployment may register additional route roots), resolves each
handler's full mounted path (router prefix + decorator path), and is
**fail-closed**: EVERY handler
must either inject+call the tenant `AuditService` OR be explicitly classified.
A route at an unrecognized path is a violation, not a silent pass. It also flags
the `_audit` / `_http_request` underscore bypass. Note the tenant `AuditService`
is required — `PlatformAuditService` (the PHI-free ops stream) does not satisfy
the check.

PHI markers (a path matching one of these may ONLY be exempted via the reviewed
`AUDIT_EXEMPT_PHI_ROUTES` list, never the non-PHI one): `/patients`, `/sessions`,
`/appointments`, `/notes`, `/transcript`, `/audio`, `/soap`, `/resolve-client`,
`/import-clients`.

## Output

Markdown list of violations plus a fix hint. Exits 0 when the tree is clean,
1 when anything is flagged.

## What it ignores

- `__init__.py` and `__pycache__`
- Functions not decorated with `@<router>.<http_method>(...)`
- Routes that inject+call the tenant `AuditService`
- Routes explicitly classified non-PHI in `AUDIT_EXEMPT_NON_PHI_ROUTES`, and the
  reviewed metadata-only PHI-marker routes in `AUDIT_EXEMPT_PHI_ROUTES`
  (both in `backend/scripts/check_route_audit.py`, each with a reason)
