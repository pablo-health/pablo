---
name: migration-lint
description: Verify every SQLAlchemy model change ships with a matching Alembic migration in the same commit. Use when the user says "run /migration-lint", is about to push a change to backend/app/db/models.py or platform_models.py, or asks why CI is complaining about model drift.
tools: [Read, Bash, Glob]
---

# Migration Lint

Enforces CLAUDE.md guardrail #4: "Models and migrations ship together."
Any change to `backend/app/db/models.py` or
`backend/app/db/platform_models.py` in the diff must be accompanied by
at least one new file under `backend/alembic/versions/` in the same
diff.

## How to run

```bash
# Default: compare HEAD to origin/main (PR-style check)
python .claude/skills/migration-lint/check.py

# Compare against a specific ref
python .claude/skills/migration-lint/check.py --base main
python .claude/skills/migration-lint/check.py --base HEAD~1

# Check the staged index only (pre-commit use)
python .claude/skills/migration-lint/check.py --staged
```

The script shells out to `git diff --name-status <base>...HEAD` (or
`--cached` for `--staged`) to get a reliable list of changed files.

## Rules

| Condition | Result |
|-----------|--------|
| Model file changed (Modified / Added), at least one new migration file added | PASS |
| Model file changed, no new migration file in diff | FAIL (exit 1) |
| Only migration files changed (e.g. a data fix) | PASS |
| No model files changed | PASS (no-op) |

## Output

One of:

- `migration-lint: no model changes - nothing to check`
- `migration-lint: OK - <N> model file(s) changed, <M> new migration(s) added`
- `migration-lint: FAIL - model files changed without a new migration:` plus
  the list of offending paths and a pointer to
  `alembic revision --autogenerate -m "<message>"`.
