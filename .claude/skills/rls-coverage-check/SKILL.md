---
name: rls-coverage-check
description: >
  Verify that every RLS-forced tenant table (any table with user_id, patient_id,
  or id column that is not in not_row_scoped) is covered by the RLS invariant
  suite and that no tenant table would be left unclassified (deny-all) by
  enable_rls_on_schema. Use when the user says "rls coverage", adds or alters a
  table in backend/app/db/models.py, asks about "has_patient_access" enforcement
  or the "fail-closed registry", or wants to check "new tenant table" safety
  before pushing.
tools: [Read, Bash]
---

# RLS Coverage Check

Enforces CLAUDE.md guardrail #4 (RLS enforcement layer): every
RLS-forced per-tenant table must be classified by
`enable_rls_on_schema` AND covered by the real-Postgres RLS invariant
suite in `backend/tests_integration/database/test_rls_invariants.py`.

## When to run

BEFORE finishing any change that:
- Adds or renames a table in `backend/app/db/models.py`
- Adds a column (`patient_id`, `user_id`, or `id`) to an existing tenant table
- Alters `enable_rls_on_schema` in `backend/app/db/__init__.py`

## How to run

```bash
# From the repo root — pure Python, no DB required
python .claude/skills/rls-coverage-check/check.py
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0    | All tables classified + covered — nothing to fix |
| 1    | One or more findings — actionable message printed to stderr |

## What it checks

1. **Unclassified tables** — any ORM table whose column shape (intersected
   to `{user_id, patient_id, id}`) would cause `enable_rls_on_schema` to
   raise `RuntimeError` (the deny-all guard). Means the table is
   force-RLS'd with no policy.

2. **Uncovered RLS-forced tables** — any table returned by
   `rls_forced_tenant_tables()` that is not in `TENANT_SCOPED_TABLES`
   in `test_rls_invariants.py` and not in `EXEMPT_RLS_FORCED_TABLES`.

## Remediation

For **unclassified tables**: add a policy branch in
`enable_rls_on_schema` (backend/app/db/__init__.py) or call
`register_overlay_not_row_scoped()` if the table's isolation boundary is
the tenant schema, not a per-row predicate.

For **uncovered tables**: add the table to `TENANT_SCOPED_TABLES` in
`backend/tests_integration/database/test_rls_invariants.py` AND add a
real-Postgres isolation test. See CLAUDE.md guardrail #4.
