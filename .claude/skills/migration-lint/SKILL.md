---
name: migration-lint
description: Check the Alembic chain before pushing — models ship with migrations, the chain has one head, and a migration touching a tenant table regenerates tenant_template.sql. Use when the user says "run /migration-lint", is about to push a change to backend/app/db/models.py, platform_models.py or backend/alembic/versions/, or asks why CI is complaining about model drift, MultipleHeads, or a stale tenant template.
tools: [Read, Bash, Glob]
---

# Migration Lint

Three cheap checks on the Alembic chain. All of them are pure file and git
inspection — no database, no container — so the whole thing runs in well under
a second and is safe to put in front of every push.

1. **Models ship with migrations** (CLAUDE.md guardrail #4). A change to
   `backend/app/db/models.py` or `backend/app/db/platform_models.py` must be
   accompanied by at least one new file under `backend/alembic/versions/`.

2. **The chain has exactly one head.** `down_revision` makes the versions
   directory a linked list, so two branches cut from the same parent leave it
   with two heads. Git merges both without a murmur and alembic then refuses to
   run at all. Nothing in a file-overlap review predicts this — the two
   migrations are two *different* files — so it has to be checked structurally.

3. **A migration touching a tenant table regenerates the template.**
   Provisioning applies `tenant_template.sql`, not the chain. A migration that
   lands without a regenerated template ships a column that exists for every
   migrated tenant and is missing from every newly provisioned one, which
   surfaces far from its cause as `column ... does not exist` in whatever test
   provisions a fresh tenant.

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

Checks 1 and 3 read `git diff --name-status <base>...HEAD` (or `--cached`).
Check 2 reads the working tree instead of the diff: on a pull request the
checkout is already main merged with the branch, which is the state a fork
would actually break.

## Rules

| Condition | Result |
|-----------|--------|
| Model file changed (Modified / Added), at least one new migration added | PASS |
| Model file changed, no new migration in diff | FAIL (exit 1) |
| Only migration files changed (e.g. a data fix) | PASS |
| No model files changed | PASS (no-op) |
| Versions directory resolves to exactly one head | PASS |
| Two or more heads, or a cycle | FAIL (exit 1) |
| Added migration touches a table the template defines, template regenerated | PASS |
| Added migration touches a table the template defines, template unchanged | FAIL (exit 1) |
| Added migration touches only platform-schema tables | PASS |

## What it deliberately does not catch

Check 3 fires only for tables `tenant_template.sql` **already defines**. That
is what keeps it free of false positives on platform-only migrations, which
legitimately leave the template alone — verified by sweeping the last 34
migration commits on main, where it flags exactly one, and that one is a real
missed regen.

The cost of that precision is two blind spots, both of which need a real
database and so belong to the `Tenant template matches alembic head` CI job
(which regenerates for real and diffs):

- **A brand-new tenant table.** It isn't in the template yet, so there is no
  name to match against.
- **A hand-edited template with its columns in the wrong order.** The file
  changed, so check 3 is satisfied, but `pg_dump` reflects `ALTER TABLE` order
  — a new column belongs at the *end* of its table, which is rarely where the
  model declares it. Generate the template; never hand-edit it.

A table name alone is also ambiguous: `users` has named both a platform table
and a per-tenant one. An explicit `schema="platform"` kwarg or a schema-
qualified raw statement is taken at its word and excluded.

## Regenerating the template

```bash
poetry run python backend/scripts/regen_tenant_template.py
```

Needs Docker (testcontainers). Re-run it after any `down_revision` re-point —
the template is captured at chain head.

## Output

One line per check, plus a remediation block on failure. Exit code is 1 if any
check failed, 0 otherwise.
