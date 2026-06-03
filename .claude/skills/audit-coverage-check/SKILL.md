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

The engine auto-detects route roots (`backend/app/routes/` in the OSS engine,
`backend/saas/**/` in the SaaS overlay), resolves each handler's full mounted
path (router prefix + decorator path), and flags any route whose URL matches a
PHI marker but is missing the `AuditService` injection or the `audit.*` call.
It also flags the `_audit` / `_http_request` underscore bypass.

PHI markers: `/patients`, `/sessions`, `/appointments`, `/notes`,
`/transcript`, `/audio`, `/soap`, `/resolve-client`, `/import-clients`.

## Output

Markdown list of violations plus a fix hint. Exits 0 when the tree is clean,
1 when anything is flagged.

## What it ignores

- `__init__.py` and `__pycache__`
- Routes whose mounted path matches no PHI marker
- Functions not decorated with `@<router>.<http_method>(...)`
- Routes explicitly classified non-PHI in `AUDIT_EXEMPT_PHI_ROUTES`
  (in `backend/scripts/check_route_audit.py`, each with a reason)
