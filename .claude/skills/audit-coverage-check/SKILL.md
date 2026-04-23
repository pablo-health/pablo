---
name: audit-coverage-check
description: Scan backend/app/routes/ for PHI-touching routes that don't inject or call AuditService. Use when the user asks to audit the HIPAA audit-logging guardrail, says "run /audit-coverage-check", or adds new routes under /patients, /sessions, /appointments, /transcript, /audio, /soap, /client.
tools: [Read, Bash, Glob]
---

# Audit Coverage Check

Enforces CLAUDE.md guardrail #1: every PHI-touching route handler in
`backend/app/routes/` must inject `audit: AuditService = Depends(get_audit_service)`
AND call `audit.<helper>(...)` in its body.

## How to run

```bash
python .claude/skills/audit-coverage-check/check.py
```

The script walks every route file under `backend/app/routes/`, resolves the
full path (router prefix + decorator path), and flags any route whose URL
touches a PHI marker but is missing either the `AuditService` injection or
an `audit.*` call.

PHI markers: `/patients`, `/sessions`, `/appointments`, `/transcript`,
`/audio`, `/soap`, `/client`.

## Output

Markdown table of `file:line | method | path | violation | quick fix`,
followed by ready-to-paste parameter / body snippets for each finding.
Exits 0 with a ✅ line when the tree is clean; exits 1 when anything is
flagged — suitable for pre-commit / CI.

## What it ignores

- `__init__.py`
- Routes whose decorator path does not match any PHI marker after
  resolving the router prefix
- Functions that are not decorated with `@router.<http_method>(...)`

## Relationship to existing guardrails

`backend/tests/test_route_audit_guardrails.py` enforces the same rule in
CI. This skill is the fast, report-shaped version you run mid-edit to
see what needs fixing before you push.
