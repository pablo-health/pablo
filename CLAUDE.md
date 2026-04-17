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
   an audit entry is a HIPAA § 164.312(b) gap.
2. **No raw SQL in route handlers.** Route handlers in
   `backend/app/routes/` must not call `session.execute(select(...))`
   directly — go through `backend/app/repositories/`. Keeps tenant
   scoping and error shapes in one place.
3. **Models and migrations ship together.** Every change to a
   SQLAlchemy model in `backend/app/db/models.py` (or
   `platform_models.py`) must include a same-commit Alembic migration.
   Don't land a model change and "add the migration later" — that
   boots a broken dev env for everyone else.
4. **PHI never enters stdout.** No `logger.info("... {patient_name}
   ...")` or `print(patient.*)` in `backend/app/`. Use `AuditService`
   for intentional PHI-adjacent records; keep everything else PHI-free.
