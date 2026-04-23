---
name: slop-sweep
description: Structured scan for AI-slop code patterns across backend + frontend. Use when the user says "run /slop-sweep", asks to review recent diffs for AI-generated cruft, or wants to sweep the tree for banner comments / as-unknown-as casts / stacked any-suppressions / mock-data imports in production.
tools: [Read, Bash, Glob]
---

# Slop Sweep

Seven-check sweep for the low-signal patterns that show up in
AI-generated diffs. Output is a prioritized findings list — high-severity
first (guardrail violations), low-severity last (chatter).

## How to run

```bash
python .claude/skills/slop-sweep/check.py
```

## Checks

| # | Severity | Pattern | Why it matters |
|---|---------|---------|----------------|
| 1 | HIGH | Underscore-prefixed `Depends(...)` parameters in `backend/app/routes/` | Silences linters on audit/http_request — CLAUDE.md guardrail #2. |
| 2 | HIGH | `mockData` imports in non-test frontend files | CLAUDE.md guardrail #6 — mock data must never ship. |
| 3 | MED | `as unknown as X` casts in TypeScript | Usually a shortcut past real typing; CLAUDE.md guardrail #7. |
| 4 | MED | `@typescript-eslint/no-explicit-any` stacked ≥2× in one file | Three or more identical casts is a refactor obligation, not a lint exception (guardrail #7). |
| 5 | MED | Hardcoded hex colors (`#abc123`) outside `tailwind.config.ts` | Pablo uses the design-system palette; raw hex is usually slop. |
| 6 | LOW | Banner comments (`# ==== FOO ====`, `// ==== FOO ====`) | No semantic value; AI padding. |
| 7 | LOW | `// TODO: consider...` / `# TODO: consider...` without an issue link | Unactionable chatter. |

## Output

Grouped by severity, one finding per line (`file:line  pattern  snippet`),
followed by a totals row. Exit code 1 if any HIGH finding, otherwise 0
(MED/LOW are reported but don't fail the command — they're for review).

## Scope

- Backend: `backend/app/**/*.py`
- Frontend: `frontend/src/**/*.{ts,tsx}` and `frontend/app/**/*.{ts,tsx}`
- Ignores `node_modules/`, `.next/`, `dist/`, `build/`, `__pycache__/`,
  and any file under a `test/` or `tests/` directory (for the mockData
  check only).
