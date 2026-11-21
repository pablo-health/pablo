# CLAUDE.md — backend/app/routes/

Route-handler guardrails from the root [`CLAUDE.md`](../../../CLAUDE.md),
kept here so they load when you work on a route. The numbers match the
root list, which keeps a one-line summary of each.

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
