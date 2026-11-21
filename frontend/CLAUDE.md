# CLAUDE.md — frontend/

Frontend rules that apply to every file under `frontend/`. The root
[`CLAUDE.md`](../CLAUDE.md) still applies.

## Frontend Conventions

- **shadcn/ui** components with Pablo brand palette (warm cream, honey, sage)
- **DM Sans** for body, **Fraunces** for headings
- See `docs/design-system/` for full design tokens

## Guardrails (load-bearing, don't skip)

The number matches the root list, which keeps a one-line summary.

6. **Mock data never ships.** Imports from frontend `mockData.ts` (or
   equivalent) in a component that is mounted in production routes
   are forbidden. Mock data lives behind `process.env.NODE_ENV !==
   'production'` or inside `src/test/`.
