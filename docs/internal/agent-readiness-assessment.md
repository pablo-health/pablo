# Agent Readiness Assessment — Pablo

**Framework:** AgentPatterns.ai Agent Readiness (L0–L5)
**Date:** 2026-05-08
**Scope:** `pablo-health/pablo` monorepo (backend, frontend, docs, infra)
**Assessor:** Claude Code (initial pass — not yet human-reviewed)

The framework scores five dimensions on an L0–L5 maturity scale:

| Level | Meaning |
|-------|---------|
| L0 | No agent support |
| L1 | Readable codebase (docs, structure) |
| L2 | Feedback loops (evals, hooks) |
| L3 | Mechanical enforcement (policies, gates) |
| L4 | Autonomous operation (self-healing) |
| L5 | Agent-first design |

## Headline Score

| Dimension | Level | One-line read |
|-----------|-------|---------------|
| Instructions | **L3** | Strong root CLAUDE.md with 8 numbered, CI-enforced guardrails; missing AGENTS.md alias and per-area nested files. |
| Harness | **L2** | `.claude/skills/` is well-developed; no `.claude/settings.json`, no hooks, no `.mcp.json`. |
| Security | **L3** | HIPAA-grade product hygiene (CodeQL, Trivy, secret scan, pentest skill, PHI guardrails) but no agent-side permission allowlist or trifecta analysis. |
| Verification | **L3** | 56 backend tests, ruff+mypy, route-audit guardrail test, migration-lint; no LLM/SOAP-output eval suite despite product being LLM-driven. |
| Observability | **L2** | AuditService + HIPAA audit logs; stdlib `logging` only, no structured logging / OpenTelemetry / agent-run telemetry. |

**Overall:** ~L2.6. The codebase is unusually disciplined for its size on the *human* SDLC dimensions (guardrails, CI, pre-commit), which carries a lot of weight here. The gaps are concentrated in (a) the agent harness itself and (b) eval coverage of the LLM-generated artifact that *is the product*.

---

## Safety Gates (framework halts on these)

| Gate | Status | Notes |
|------|--------|-------|
| Secrets in agent context | ✅ Clear | Pre-commit `detect-private-key`, secret-scanning workflow, `gitleaks` history clean per CI. No secrets observed in `CLAUDE.md` or skill files. |
| Lethal trifecta (private data + untrusted input + egress) | ⚠️ Plausible | Pablo agents can read PHI (sessions, transcripts, SOAP notes), accept *untrusted* input (patient-uploaded transcripts can contain prompt-injection payloads), and have egress (LLM APIs, GCP). No documented decomposition. **Action:** explicitly map which agent surfaces have all three legs, document mitigation. |
| High injection surface on rule files | 🟡 Partial | `CLAUDE.md` lives in repo and any contributor PR can mutate it. No CI gate yet on `CLAUDE.md` / `.claude/skills/*` diffs requiring code-owner review. |

None are hard-halt today, but the trifecta and rules-file integrity warrant treatment before raising autonomy.

---

## Dimension-by-dimension

### 1. Instructions — L3

**Present**
- `CLAUDE.md` at repo root with: product overview, tech stack, engineering philosophy, code-quality rules, commands, backend/frontend conventions, and 8 numbered "load-bearing" guardrails (each with the file/CI gate that enforces it).
- `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CLA.md` at root.
- `docs/` covers `HIPAA_SECURITY.md`, `HIPAA_AUDIT_LOGS.md`, `TECHNICAL_ARCHITECTURE.md`, `GCP_DEPLOYMENT.md`, `SELF_HOSTING_HIPAA_GUIDE.md`, `compliance/`, `internal/`.
- 5 skills under `.claude/skills/` with proper SKILL.md frontmatter (`name`, `description`, `tools`): `audit-coverage-check`, `phi-log-scan`, `pentest`, `migration-lint`, `slop-sweep`.

**Gaps**
- No `AGENTS.md` alias. Cross-vendor agents (Cursor, Codex, Continue, Aider) look for `AGENTS.md` first; Pablo only ships `CLAUDE.md`.
- No `llms.txt` for discoverability of the LLM/agent surface.
- No nested instructions for `backend/`, `frontend/`, `docs/design-system/` despite the root file repeatedly pointing at them. An agent editing only `frontend/` pulls the full root file but no frontend-specific context.
- No documented "rule budget" review — `CLAUDE.md` is currently dense but coherent; needs a periodic review cadence to prevent drift.
- Skills are well-described but a couple (`pentest`, `slop-sweep`) bundle multiple actions — could be split for sharper agent dispatch.

### 2. Harness — L2

**Present**
- `.claude/skills/` directory with executable `check.py` per skill.
- `.pre-commit-config.yaml` wires the audit-param check and migration-lint into the local commit loop *and* CI uses the same scripts (single source of truth).
- `Makefile` exposes `make check` / `lint` / `test` / `format` so agents have a uniform entry point.
- GitHub Actions: `ci.yml`, `security.yml`, `codeql.yml`, `deploy.yml`, `release.yml`, `base-image-bump.yml`.

**Gaps**
- **No `.claude/settings.json`.** Means: no hooks (`PreToolUse`, `PostToolUse`, `Stop`, `UserPromptSubmit`, `SessionStart`), no permission allowlist, no env defaults, no statusline. Every agent runs with default-permissive prompts.
- **No `.mcp.json`** at repo root. The session’s GitHub MCP tools are environment-injected, not repo-pinned, so contributors don’t get the same surface.
- No `SessionStart` hook to verify `poetry install`, DB up, or env wiring before an agent starts editing.
- No `PreToolUse` hook protecting destructive Bash (e.g. `gcloud … delete`, `git push --force`, `rm -rf`).
- No skill template / scaffolding doc — adding the next skill is copy-from-existing.

### 3. Security — L3

**Present**
- `SECURITY.md`, HIPAA-grade documentation in `docs/`, BAA manifest in `docs/compliance/`.
- CI: CodeQL, Trivy (deps + base images + Dockerfile config), secret-scanning, dependabot.
- Pre-commit: `detect-private-key`, `check-added-large-files` (500kb), merge-conflict, ruff, mypy.
- Product-level guardrails directly relevant to agent safety:
  - **#1** PHI routes must inject AuditService (CI-enforced).
  - **#2** No `_audit` underscore-prefix bypass (pre-commit + CI).
  - **#3** No raw SQL in route handlers.
  - **#5** No PHI in stdout (skill: `/phi-log-scan`).
  - **#7** Type-cast escape hatches need a written reason (skill: `/slop-sweep`).
- `pentest` skill + `scripts/pentest/` weekly cron-style routine against deployed Cloud Run.

**Gaps**
- **No agent-side permission allowlist.** Without `.claude/settings.json`'s `permissions.allow/deny`, an agent can run `gcloud run services delete` or `psql … DROP TABLE` with only the user’s in-the-moment approval.
- **No trifecta decomposition** documented. Transcripts ingested from patients are *untrusted input* with PHI semantics, and the SOAP-note generator has LLM egress. The current guardrails address PHI accounting but not prompt-injection from transcript content.
- **No CODEOWNERS gate on `CLAUDE.md` / `.claude/skills/*`**. A contributor PR can quietly weaken the rules an agent reads.
- No documented credential-handling story for agents (where the agent gets DB creds, GCP SA keys, LLM API keys when acting on the user’s behalf).

### 4. Verification — L3

**Present**
- 56 test files in `backend/tests/`, including:
  - `test_route_audit_guardrails.py` — enforces guardrail #1 mechanically.
  - `test_audit_repo.py`, `test_audit_service.py`, `test_audit_review_service.py`, `test_closed_loop_audit.py`, `test_audit_retention_cron.py`, `test_hard_purge_cron.py`, `test_hipaa_attestation.py`, `test_hipaa_log_review.py`.
  - Domain coverage on auth, calendar/iCal sync, scheduling, BM25/embedding/NLI services, note generation/type/service, ext auth, middleware, migrations.
- `make check` (lint + test) is the documented gate before push.
- Migration-lint enforces "model + migration ship together" (guardrail #4).
- `audit-coverage-check` and `phi-log-scan` skills act as agent-runnable verifiers.

**Gaps**
- **No LLM-output eval suite.** SOAP-note generation is the product, README claims "dual-method verification (LLM + classical NLP), zero hallucinations", but there is no `evals/` directory with pinned transcripts → expected SOAP output, no regression suite for prompt changes, no scorecard. `test_note_generation_service.py` and `test_nli_service.py` likely cover the *service plumbing*, not output quality at the prompt-and-model level.
- No agent-action eval (does the SOAP agent reliably refuse out-of-scope edits, etc.).
- No `frontend/` test inventory captured here — vitest is referenced; coverage delta vs backend is unknown.
- No documented "what does CI actually fail on" matrix — useful for agents deciding when to retry vs escalate.

### 5. Observability — L2

**Present**
- `AuditService` and `platform_audit_service` — domain-specific structured audit trail.
- `docs/HIPAA_AUDIT_LOGS.md` documents the audit event surface.
- `request_context.py` and `middleware.py` exist (request-scoped context).
- CI surfaces SARIF to GitHub code-scanning (CodeQL + Trivy categories).

**Gaps**
- Backend uses stdlib `logging` only — no `structlog`, no OpenTelemetry, no Sentry observed. Logs are unstructured, which (a) makes incident triage harder and (b) makes the "no PHI in stdout" guardrail rely on a static scanner rather than a structured-redaction layer.
- No agent-run telemetry: no record of which skill ran, exit code, what files it touched, or PR linkage.
- No incident pipeline doc for agent-induced regressions (rollback, post-mortem template).
- No metrics/SLO doc visible for SOAP-generation latency, hallucination rate, audit-log write success.

---

## Prioritized punch list

Format: `[severity, ease] task`. Severity: 🔴 high / 🟠 med / 🟡 low. Ease: 🟢 easy / 🟡 moderate / 🔴 hard.

### Quick wins (do first)

1. 🟠🟢 **Add `AGENTS.md` symlink → `CLAUDE.md`** (or duplicate). One-line cross-vendor compatibility.
2. 🟠🟢 **Create `.claude/settings.json`** with: a default-deny permission set, allowlist for `make *`, `poetry run *`, common `git`/`gh` reads, and a `Stop`/`PostToolUse` hook running `make lint` on touched files.
3. 🟠🟢 **CODEOWNERS for rule integrity:** require code-owner review on `CLAUDE.md`, `.claude/**`, `.pre-commit-config.yaml`, `scripts/check_audit_params.py`.
4. 🟡🟢 **Add `llms.txt`** at repo root pointing to README, CLAUDE.md, docs/TECHNICAL_ARCHITECTURE.md, HIPAA guides.
5. 🟡🟢 **Document "what CI fails on"** matrix in `docs/internal/` so agents know retryable vs human-needed failures.

### Medium effort

6. 🔴🟡 **Trifecta decomposition doc** — for each agent surface (SOAP generation, calendar sync, transcript ingestion), name: data classification, input trust level, egress paths, mitigation. Then add a `PreToolUse` hook that blocks LLM egress when transcript text crosses an allow-list.
7. 🔴🟡 **LLM eval harness** — `evals/soap_notes/` with pinned (transcript → expected SOAP) fixtures, a hallucination-rate scorer, and a CI job that fails on regression. This is the largest gap given the product description.
8. 🟠🟡 **Per-area nested instruction files**: `backend/CLAUDE.md` (poetry, Alembic flow, repository pattern, test conventions), `frontend/CLAUDE.md` (shadcn/Tailwind tokens, mockData rules), `docs/design-system/CLAUDE.md`.
9. 🟠🟡 **Structured logging migration** — adopt `structlog` with a PHI-redaction processor; replace stdlib `logging.getLogger` calls; this turns guardrail #5 from a static scanner into a runtime invariant.
10. 🟠🟡 **`SessionStart` hook** to assert `poetry install` parity, DB reachable, env vars present — saves agents from chasing ghosts.

### Larger investments

11. 🟠🔴 **OpenTelemetry tracing** for FastAPI + Vertex/Anthropic clients. Pairs with #9 for agent-friendly debugging.
12. 🟠🔴 **Agent-run telemetry** — skill invocations, files touched, exit codes; surfaced in `docs/internal/` for retro.
13. 🟡🔴 **Skill split**: break `pentest` and `slop-sweep` into narrower skills so dispatch is unambiguous.
14. 🟡🔴 **Frontend evals** — visual regression / Playwright for the SOAP review UI; today the agent has no way to verify a UI change beyond `npm run build`.

---

## Open questions for the team

- Does the existing "dual-method verification (LLM + classical NLP)" framing have a measurable test artifact, or is it implemented at runtime only? (Determines whether #7 is "build" or "expose".)
- Should agent permissions be repo-level (`.claude/settings.json` checked in) or developer-level? Pablo’s HIPAA posture argues for repo-level + signed.
- Who owns `CLAUDE.md` / skill changes? CODEOWNERS recommendation in #3 needs a name.
- Acceptable LLM egress paths for agents working on this repo: Vertex only? Anthropic too? Internal-only?

---

## Re-assessment cadence

Re-run this assessment after #1–#5 (quick wins). Target end-state in 60 days: **L3 across all five dimensions**; L4 deferred until eval harness (#7) has 30 days of green history.
