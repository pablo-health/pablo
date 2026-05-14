# CI Failure Matrix

When CI goes red on a Pablo PR, this is the lookup table for *what to do
next*. Agents and humans both work from this — it tells you which failures
are retryable, which are an obvious local fix, and which need a human in the
loop.

If a failure mode isn't on this table, **do not retry blindly**. Add it.

## Conventions

- **Action: retry** — flake; rerun the job once. If it fails twice, treat as a real failure and reclassify.
- **Action: fix locally** — agent (or human) can fix directly without escalation. Run `make check` first to reproduce.
- **Action: escalate** — needs a human decision. Don't push a fix that papers over the symptom.
- **Action: investigate** — root cause unclear. Read logs, then reclassify before acting.

CI surface (see `.github/workflows/ci.yml`):

| Workflow | Job |
|----------|-----|
| `ci.yml` | `version-check`, `migration-lint`, `backend`, `backend-integration`, `frontend` |
| `security.yml` | `trivy-deps`, `trivy-base-images`, `trivy-config`, secret scanning |
| `codeql.yml` | CodeQL (Python, JS) |
| `deploy.yml` | Deploy to Cloud Run (main only) |
| `release.yml` | Tag + publish container images |

## Backend

| Symptom | Likely cause | Action |
|---------|-------------|--------|
| `ruff check` fails with `F401`, `E501`, etc. | Lint regression. | **Fix locally.** `poetry run ruff check --fix backend/` then commit. |
| `ruff format` would reformat | Unformatted code. | **Fix locally.** `poetry run ruff format backend/` then commit. |
| `mypy` error in changed file | Real type error. | **Fix locally** unless the error is in code you didn't touch — then **investigate** for a third-party stub regression. |
| `mypy` error in *unchanged* file after dep bump | Stub drift from a dependency upgrade. | **Investigate.** Pin the offending stub or update the type. Don't add `# type: ignore` without a one-line reason (guardrail #7). |
| `pytest` fails, single test, async/timing related | Flake. | **Retry once.** If it fails twice on the same test, reclassify as a real failure. |
| `pytest` fails on `test_route_audit_guardrails.py` | New PHI route is missing `audit: AuditService = Depends(...)` or an `audit.log_*` call (guardrail #1). | **Fix locally.** Inject and call. See `.claude/skills/audit-coverage-check`. |
| `pytest` fails on `tests_integration/database/test_audit_writes.py` | Audit-write contract regression. | **Escalate** — this is the HIPAA-relevant write path. |
| `pre-commit` `no-underscore-audit-params` (also runs in CI) | Route handler uses `_audit` / `_http_request` (guardrail #2). | **Fix locally.** Remove the underscore prefix or remove the param if the route is non-PHI. If non-PHI, ask a human. |
| `migration-lint` fails | Model changed but no Alembic migration in same commit (guardrail #4). | **Fix locally.** Generate the migration with `cd backend && poetry run alembic revision --autogenerate -m "..."`, review, commit. |
| `pip-audit` finds CVE | Vulnerable dep. | **Fix locally** if a patch version exists. **Escalate** if no patch (need risk-accept decision). |
| `Cache restore failed` / `network` in setup steps | Transient runner / GHA flake. | **Retry.** |
| `Free disk space` step fails | Runner-side. | **Retry.** |

## Frontend

| Symptom | Likely cause | Action |
|---------|-------------|--------|
| `tsc --noEmit` fails | Real TS error. | **Fix locally.** No `as any` / `@ts-expect-error` without a one-line reason (guardrail #7). |
| `npm run lint` fails | ESLint regression. | **Fix locally.** |
| `vitest` flake | Timing / DOM-cleanup. | **Retry once.** Flag if reproducible. |
| `npm audit --audit-level=high` fails | Vulnerable npm dep. | **Fix locally** with `npm update <pkg>` or pinned upgrade. **Escalate** if breaking change. |
| `npm ci` network error | Registry flake. | **Retry.** |
| `slop-sweep` flags `mockData` import in production component (guardrail #6) | Mock data leaked into production path. | **Fix locally.** Move behind `process.env.NODE_ENV !== 'production'` or into `src/test/`. |

## Security workflows

| Symptom | Likely cause | Action |
|---------|-------------|--------|
| `trivy-deps` HIGH/CRITICAL on a new dep we just added | Don't merge it. | **Fix locally** (downgrade or swap dep) or **escalate** if no alternative. |
| `trivy-deps` HIGH/CRITICAL on an existing dep, no PR change | Newly disclosed CVE. | **Investigate.** Open a tracking issue if no fix yet. Don't block unrelated PRs. |
| `trivy-base-images` HIGH/CRITICAL on Chainguard image | Base bumped upstream. | **Investigate.** `base-image-bump.yml` should pick this up; if not, file an issue. |
| `trivy-config` flags new Dockerfile pattern | Hardening regression. | **Fix locally.** |
| `detect-private-key` (pre-commit) hits | Real key in diff. | **Halt + escalate.** Rotate the key, rewrite history with care. Do not just `git rm` and retry. |
| `secret-scanning` (GitHub) hits | Same as above. | **Halt + escalate.** |
| CodeQL `cwe-*` finding | Real or false positive. | **Investigate** the alert. Triage on the security tab; don't dismiss without a written reason. |

## Version + release

| Symptom | Likely cause | Action |
|---------|-------------|--------|
| `version-check` fails | `VERSION`, `pyproject.toml`, `frontend/package.json`, and `min_client_versions.json` are out of sync. | **Fix locally.** Run `bash scripts/check-version-sync.sh` to see exactly which file. |
| `release.yml` fails after tag | Container build / registry push issue. | **Investigate.** Often transient on GHCR; retry once. **Escalate** if signed-image verification fails (supply-chain signal). |
| `deploy.yml` fails on Cloud Run rollout | App is unhealthy on the new revision. | **Halt + escalate.** Cloud Run will keep serving the previous revision; do not force-promote. Roll back, then debug. |

## Pre-commit (developer-side, surfaces in CI as a guardrail)

| Symptom | Cause | Action |
|---------|-------|--------|
| `trailing-whitespace`, `end-of-file-fixer`, `check-yaml`, `prettier` | Cosmetic. | **Fix locally** by re-running pre-commit. |
| `check-added-large-files` (>500kb) | Probably a binary that shouldn't be in git. | **Investigate** before forcing through. |

## When to NOT just retry

- **Anything in the audit / HIPAA / migration / secrets path.** Retry-on-flake is for orthogonal infra; for these surfaces, a "retry that passes" can mask a real correctness regression.
- **A test that was green on a previous SHA.** Bisect, don't retry-loop.
- **Two retries in a row.** That's not a flake — it's a real failure presenting intermittently.

## Updating this matrix

Add a row when a new failure mode shows up twice. Remove a row only when the
underlying check is removed from CI. Keep the matrix in lockstep with
`.github/workflows/` — if you add a job, add its likely failure modes here.
