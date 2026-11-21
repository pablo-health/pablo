# CLAUDE.md

## Product: Pablo

**Pablo** = **P**aperwork **A**utomation for **B**ehavioral **L**ogging & **O**utcomes

AI-powered practice software for therapists — session notes drafted from transcripts, plus scheduling, billing and the inbox.

### Tech Stack
| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 16, TypeScript, Tailwind CSS, shadcn/ui |
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

## Rules that live beside the code

A `CLAUDE.md` in a subdirectory loads once you read or edit a file there, and
each has an `AGENTS.md` symlink. A guardrail about one place is written out in
full there; its numbered line below is enough to follow before that loads.

- [`backend/CLAUDE.md`](backend/CLAUDE.md) — backend conventions: Python 3.13+, Poetry (always `poetry run`), type annotations, Pydantic, `Depends()`. Below it, [`backend/app/routes/CLAUDE.md`](backend/app/routes/CLAUDE.md) holds guardrails 1–3 and [`backend/app/db/CLAUDE.md`](backend/app/db/CLAUDE.md) guardrail 4.
- [`frontend/CLAUDE.md`](frontend/CLAUDE.md) — frontend conventions (shadcn/ui, brand palette, fonts) and guardrail 6.

## Writing copy a clinician will read

**Before writing or editing any user-facing string — UI, empty state, error,
onboarding — read [`docs/reference/copy-style.md`](docs/reference/copy-style.md).**

The principle, so it is not a surprise: **keep the safety model, simplify what
the reader sees.** Pablo touches how a clinician gets paid, so the reasoning
behind a screen is usually careful. The failure mode is making every sentence
carry that reasoning — long, defensive copy that exposes billing machinery
early and starts contradicting itself. The reasoning goes in the code comment
and the design doc. The screen gets the conclusion.

Four that are easy to get wrong:

- **Never claim readiness the system has not checked.** Reaching the end of a
  flow is not being finished — ask the data.
- **Never recite what will not happen.** It introduces machinery the reader had
  not thought about and makes a safe default sound dangerous.
- **Never imply someone should leave a service they use** (Headway, Alma, Rula
  and the like). Staying is a good outcome.
- **No gendered pronouns** for a clinician or client. Second person usually
  solves it; otherwise *they*.

## Guardrails (load-bearing, don't skip)

1. **Audit every PHI route.** A route in `backend/app/routes/` that touches patients, sessions, soap_notes or appointments takes `audit: AuditService = Depends(get_audit_service)` and calls the matching `audit.log_*` before returning — full rule in [`backend/app/routes/CLAUDE.md`](backend/app/routes/CLAUDE.md).
2. **No underscore-prefixed `_audit` / `_http_request` parameters.** The `_` marks the value unused and silently bypasses #1 — full rule in [`backend/app/routes/CLAUDE.md`](backend/app/routes/CLAUDE.md).
3. **No raw SQL in route handlers.** No `session.execute(select(...))` in a route; go through `backend/app/repositories/` — full rule in [`backend/app/routes/CLAUDE.md`](backend/app/routes/CLAUDE.md).
4. **Models and migrations ship together.** A model change carries a same-commit migration in its own chain (`models.py` → `backend/alembic/`, `platform_models.py` → `backend/alembic_platform/`; platform DDL never goes in the tenant chain), the regenerated template for whichever chain you touched (`backend/scripts/regen_tenant_template.py` / `regen_platform_schema.py`), and an RLS classification for any new tenant table — full rule, migration order and RLS layers in [`backend/app/db/CLAUDE.md`](backend/app/db/CLAUDE.md).
5. **PHI never enters stdout.** No `logger.info("... {patient_name}
   ...")` or `print(patient.*)` in `backend/app/`. Use `AuditService`
   for intentional PHI-adjacent records; keep everything else PHI-free.
6. **Mock data never ships.** No import from `frontend/src/lib/mockData.ts` (or its like) in a component mounted in a production route — full rule in [`frontend/CLAUDE.md`](frontend/CLAUDE.md).
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
    longer auto-staged. The published board export lives on the
    `beads-metadata` branch, not main (`git fetch origin beads-metadata
    && git show FETCH_HEAD:.beads/issues.jsonl`); on main the file is
    untracked and ignored, and must not be committed back. When
    committing code, `git add` explicit paths (never `git add -A` /
    `git add .`) so that churn stays out of your commit.
    If beads files ever sneak into the index, `git restore --staged`
    them.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
## How `main` is governed

`main` merges on green. A pull request needs the required checks to pass and
**zero** approvals, so ordinary code lands without anyone clicking anything.

The exception is every path in [`.github/CODEOWNERS`](.github/CODEOWNERS): the
agent rules, CI and workflows, the audit-logging surface, the schema and
migrations, and the deploy surface. A pull request touching any of them waits
for `@kurtn-pablo`'s review ("Require review from Code Owners" is on, and a
new push dismisses an earlier approval). Read that file's header for why the
list is short and has no `*` fallback.

For agents working in this repo:

- **Human-merged does not mean human-edited.** You may, and should, edit
  code-owned paths yourself and open the pull request. Do not hand the
  maintainer a diff, a snippet or a "someone needs to add..." note instead;
  the required review is the gate, not who typed the change.
- On a pull request that touches a code-owned path: **never arm auto-merge,
  never merge with `--admin`, and never merge it yourself.** Open it, say so
  in the body, and leave the merge to the code owner.
- On any other pull request, merge only once the required checks are green
  at the head commit. No `--admin` merges, ever: if a check will not report,
  that is a CI bug to fix, not a gate to step around.

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

### Worktrees

After creating a git worktree, run `scripts/beads-worktree-sync.sh` from inside it before trusting any `bd` output there. A fresh worktree starts from the committed `.beads/metadata.json` (embedded mode) rather than the main checkout's local override, so without the sync `bd` reads a stale snapshot and its writes never reach the shared board. Confirm `.beads/metadata.json` in the worktree shows the same `dolt_mode` as the main checkout.

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
