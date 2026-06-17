# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Guardrail tests: every API route must declare its security posture
explicitly via one of four marker dependencies.

Sibling to ``test_route_audit_guardrails.py``. Audit + MFA are both
route-level security guardrails — these tests catch the
"forgot a decorator on a new route" failure mode at PR time, instead
of relying on a runtime alert that is structurally blind to the
missing-guard case (a runtime alert can only fire on code paths that
already have the MFA gate installed, so the route the developer
forgot to guard would never emit any bypass-attempt log).

Design:

- Imports the FastAPI app (DB mocked in conftest.py) and walks
  ``app.routes`` at runtime. That is the canonical inventory — every
  reachable HTTP endpoint is in there, however it got registered
  (decorators, ``include_router``, ``add_api_route``, factories, …).
- For each route, recursively inspects ``route.dependant`` looking
  for exactly one of four marker callables:

    1. ``require_mfa`` — MFA-required (the default; reached
       transitively via ``get_current_user``, ``get_tenant_context``,
       ``require_admin``, etc.).
    2. ``get_current_user_no_mfa`` — pre-MFA-enrollment onboarding.
    3. ``require_pentest_runner`` — service-account ID-token auth.
    4. ``truly_public`` — intentionally public, no auth.

- A route that has NONE of the four in its dependency tree fails the
  test. There is no allowlist — every route declares its posture at
  the route itself, in the diff at PR time.

Adding a new route without one of these markers means a missing
security decision. Fix it by adding the appropriate ``Depends(...)``
to the handler signature, then re-run the test.
"""

from __future__ import annotations

# conftest.py mocks the DB engine + session factory and sets
# ENVIRONMENT=development before any app code is imported, so this
# import does not need a live Postgres.
from app.auth.route_security import truly_public
from app.auth.service import (
    get_current_user_no_mfa,
    require_cloud_tasks_invoker,
    require_mfa,
    require_pentest_runner,
)
from app.main import app
from fastapi.routing import APIRoute

# The four marker postures that classify a route's security posture. A posture
# may be satisfied by any one of several marker callables (e.g. there is more
# than one service-account auth dependency); each value is the tuple of
# callables that count for that posture.
SECURITY_MARKERS: dict[str, tuple] = {
    "mfa-required": (require_mfa,),
    "pre-mfa-onboarding": (get_current_user_no_mfa,),
    "service-account-auth": (require_pentest_runner, require_cloud_tasks_invoker),
    "truly-public": (truly_public,),
}


def _has_dependency(dependant, target_callables: tuple) -> bool:
    """True if any of ``target_callables`` appears anywhere in this tree."""
    if dependant.call in target_callables:
        return True
    return any(_has_dependency(sub, target_callables) for sub in dependant.dependencies)


def _classify(route: APIRoute) -> str | None:
    """Return the first matching marker name, or None if the route is
    not classified by any of them."""
    for name, marker in SECURITY_MARKERS.items():
        if _has_dependency(route.dependant, marker):
            return name
    return None


def _iter_api_routes():
    """Yield (route, method) for every real APIRoute on the app.

    Method aliases (``HEAD`` synthesized from ``GET``) are skipped so a
    single endpoint counts once, not twice.
    """
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            if method == "HEAD":
                continue
            yield route, method


def test_every_route_has_a_security_marker() -> None:
    """Catch the dominant failure mode — forgetting MFA on a new route.

    Every API route on the app must declare its security posture via
    one of the four marker dependencies in ``SECURITY_MARKERS``. There
    is no allowlist; the security decision lives at the route, in the
    diff at PR time.

    To fix a failure: add one of these to the route handler's
    signature, picking the one that matches the actual posture:

        ``user = Depends(get_current_user)``      → MFA-required
        ``user = Depends(get_current_user_no_mfa)`` → pre-MFA onboarding
        ``actor = Depends(require_pentest_runner)`` → service-account auth
        ``_public: None = Depends(truly_public)`` → intentionally public
    """
    unclassified: list[str] = []
    for route, method in _iter_api_routes():
        if _classify(route) is None:
            unclassified.append(
                f"  {method:6s} {route.path}  (handler: {route.endpoint.__qualname__})"
            )

    assert not unclassified, (
        "Routes have no security-posture marker in their dependency tree. "
        "Add one of:\n"
        "  • Depends(get_current_user) / get_tenant_context / require_admin "
        "(MFA-required — the default for any user-facing route)\n"
        "  • Depends(get_current_user_no_mfa) (pre-MFA-enrollment onboarding)\n"
        "  • Depends(require_pentest_runner) (service-account auth)\n"
        "  • Depends(truly_public) (intentionally public — health, pre-auth, "
        "self-verifying webhooks, IAP-gated)\n\n"
        "Routes missing a marker:\n" + "\n".join(unclassified)
    )


def test_security_posture_inventory_is_documented() -> None:
    """Snapshot the security-posture breakdown so a reviewer can see
    in one glance whether new routes shifted the mix.

    This test always passes; it exists to surface the inventory in
    pytest output (run with ``-s`` to see the counts).
    """
    counts: dict[str, int] = dict.fromkeys(SECURITY_MARKERS, 0)
    for route, _method in _iter_api_routes():
        marker = _classify(route)
        if marker is not None:
            counts[marker] += 1

    print("\nRoute security-posture inventory:")
    for name, count in counts.items():
        print(f"  {name:24s} {count}")
    print(f"  {'total':24s} {sum(counts.values())}")
