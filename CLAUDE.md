# CLAUDE.md

## Product: Pablo

**Pablo** = **P**aperwork **A**utomation for **B**ehavioral **L**ogging & **O**utcomes

AI-powered therapy documentation — SOAP note generation from session transcripts, with dual-method verification (LLM + classical NLP).

### Tech Stack
| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 15, TypeScript, Tailwind CSS, shadcn/ui |
| Backend | FastAPI, Python 3.13, PostgreSQL (Cloud SQL) |
| Infra | Docker, Google Cloud Run, GitHub Actions CI/CD |

## Engineering Philosophy

- **Quality over speed** — cleaner, modern, readable code
- **DRY** — Extract common patterns into helpers/utilities
- **Self-documenting code** — Clear names eliminate most comments
- **One file per domain concept** — Keep files focused and under 400 lines
- **Keep solutions minimal** — Don't add abstractions unless explicitly needed

## Code Quality

- Follow existing patterns in the codebase
- Don't add error handling for scenarios that can't happen
- Only add comments where logic isn't self-evident
- If removing unused code, delete it completely

## Commands

```bash
make check      # Lint + test (run before any PR)
make lint       # Ruff + mypy (backend), eslint (frontend)
make test       # Pytest (backend), vitest (frontend)
make format     # Auto-fix formatting
```

## Backend Conventions

- **Python 3.13+** with Poetry — always use `poetry run`
- **Type annotations** everywhere: `str | None` not `Optional[str]`
- **Pydantic models** for API request/response validation
- **FastAPI `Depends()`** for dependency injection

## Frontend Conventions

- **shadcn/ui** components with Pablo brand palette (warm cream, honey, sage)
- **DM Sans** for body, **Fraunces** for headings
- See `docs/design-system/` for full design tokens

## Guardrails (load-bearing, don't skip)

1. **Audit every PHI route.** New routes in `backend/app/routes/` that
   touch patients, sessions, soap_notes, or appointments must take
   `audit: AuditService = Depends(get_audit_service)` and call the
   matching `audit.log_*` helper before returning. PHI access without
   an audit entry is a HIPAA § 164.312(b) gap. Enforced in CI by
   `backend/tests/test_route_audit_guardrails.py`.
2. **No underscore-prefixed `_audit` / `_http_request` parameters.**
   The `_` prefix tells Python and every linter the value is
   intentionally unused — a silent bypass of guardrail #1. If the
   route genuinely doesn't need audit, it probably isn't a PHI route;
   talk to a human before adding one with that pattern. Enforced at
   pre-commit time by `scripts/check_audit_params.py` and in CI.
3. **No raw SQL in route handlers.** Route handlers in
   `backend/app/routes/` must not call `session.execute(select(...))`
   directly — go through `backend/app/repositories/`. Keeps tenant
   scoping and error shapes in one place.
4. **Models and migrations ship together.** Every change to a
   SQLAlchemy model in `backend/app/db/models.py` (or
   `platform_models.py`) must include a same-commit Alembic migration.
   Don't land a model change and "add the migration later" — that
   boots a broken dev env for everyone else. **Same commit, regenerate
   the tenant template.** Any alembic revision that touches tenant DDL
   (`backend/alembic/versions/`) requires regenerating
   `backend/app/db/tenant_template.sql`:

   ```
   poetry run python backend/scripts/regen_tenant_template.py
   ```

   The template is the canonical schema applied to every freshly-
   provisioned tenant — `create_practice_schema` reads it directly
   (it does **not** run alembic per tenant, by design). DDL outside
   the ORM (functions, triggers, custom types, raw `op.execute(...)`
   SQL) is invisible to `Base.metadata.create_all`; if you forget to
   regenerate, new tenants land at HEAD-stamped but **missing your
   DDL**, and every code path that touches it 500s. This is exactly
   how patient-create regressed on 2026-05-17. CI should diff the
   committed template against a freshly-regenerated copy — until
   that check exists, regenerate manually and commit the resulting
   `tenant_template.sql` alongside the migration.

   **RLS enforcement** — every per-tenant table that carries a
   `user_id`, `patient_id`, or `id` column is force-RLS'd by
   `enable_rls_on_schema`. A newly-added table MUST have a policy
   branch in that function or be explicitly listed as not-row-scoped
   via `register_overlay_not_row_scoped()`, otherwise it silently
   ships as deny-all. That isolation contract is verified at
   **three layers**:

   - **L2 real-time hook** — `.claude/settings.json` PostToolUse hook runs
     `poetry run python .claude/skills/rls-coverage-check/check.py`
     automatically after any Edit/Write to `backend/app/db/models.py`.
   - **L3 unit guard (CI)** — `backend/tests/test_enable_rls_policy_coverage.py`
     contains `test_every_real_tenant_table_is_classified` (iterates real ORM
     metadata to catch unclassified tables before integration) and
     `test_rls_forced_tables_are_covered_by_rls_invariants` (asserts the
     invariant suite covers every auto-derived RLS-forced table).
   - **L3 agent skill** — run `python .claude/skills/rls-coverage-check/check.py`
     before finishing any change that adds or alters a per-tenant table.
   - **L4 self-healing** — `rls_forced_tenant_tables()` in
     `backend/app/db/__init__.py` derives the set of every RLS-forced tenant table from the ORM,
     and `test_rls_invariants.py` unions it into `_EFFECTIVE_TABLES` so
     a new RLS-bearing table (patient-access, user-owned, or special-cased)
     is automatically covered by the RLS-forced and fail-closed invariants
     with zero manual edits.

   Any new patient-scoped table needs an integration test that seeds two
   clinicians and asserts one cannot read or write the other's patient's rows.

5. **PHI never enters stdout.** No `logger.info("... {patient_name}
   ...")` or `print(patient.*)` in `backend/app/`. Use `AuditService`
   for intentional PHI-adjacent records; keep everything else PHI-free.
6. **Mock data never ships.** Imports from frontend `mockData.ts` (or
   equivalent) in a component that is mounted in production routes
   are forbidden. Mock data lives behind `process.env.NODE_ENV !==
   'production'` or inside `src/test/`.
7. **Type-cast escape hatches require a reason.** Every `as any`,
   `as unknown as X`, `@ts-ignore`, `@ts-expect-error`, or
   `# type: ignore` must be followed by a one-line comment naming the
   library limitation or refactor debt it represents. Three or more
   identical casts in a file is a refactor obligation, not a lint
   exception.
8. **`make check` passes locally before you push.** CI is the
   backstop, not the primary loop. Pre-commit hooks (see
   `.pre-commit-config.yaml`) catch the cheap regressions so you don't
   wait on CI to learn you forgot to run ruff.

   **For AI agents specifically:** `pytest` and `ruff check` alone are
   *not* "CI green." CI runs `make lint` which includes **mypy** — a
   different class of error (abstract async-generator signatures,
   `Literal` narrowing, missing param annotations, invalid `type:
   ignore` comments) that ruff happily lets through. Before declaring
   "CI should be green" or asking the user to merge, run
   `make check` (= `make lint` + `make test`). Reporting "pytest
   passed, ruff passed" is not a substitute.

9. **Diagnostic compares against another ref use `git show`, not
   `git checkout <ref> -- <path>`.** The checkout form rewrites your
   working tree with the other ref's content and is easy to clobber
   uncommitted work with. Use `git show <ref>:path/to/file` for a
   single file, or `git worktree add /tmp/refname <ref>` for a whole
   tree you can `cd` into. Never use `git checkout main -- backend/`
   to "quickly see what main looks like" — that's a foot-gun, not a
   diff.

10. **Don't let beads state ride into code commits.** `core.hooksPath`
    is set to `.beads/hooks/`, so a `pre-commit` hook runs on every
    commit and a fresh worktree's `post-checkout` dirties the beads
    DB. `export.git-add` is set to `false` (`bd config get
    export.git-add`), so the auto-exported `.beads/issues.jsonl` is no
    longer auto-staged — but it still shows as modified in `git
    status`. When committing code, `git add` explicit paths (never
    `git add -A` / `git add .`) so that churn stays out of your commit.
    If beads files ever sneak into the index, `git restore --staged`
    them.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
