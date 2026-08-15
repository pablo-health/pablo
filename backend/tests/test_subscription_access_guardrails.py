# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Guardrail tests: every API route must declare where it stands with
respect to the subscription gate.

Sibling to ``test_route_mfa_guardrails.py``, and the same shape of
guardrail: walk the live route table, refuse to let a route be
*unclassified*. The failure mode here is quieter than the MFA one.
Forgetting the subscription gate on a new route does not break
anything visibly — the route just works, for everyone, forever. It
only shows up as a route that kept accepting new records after a
practice's subscription wound down, which nobody is looking for.

Every ``(route, method)`` must be one of three things:

1. **Gated** — ``require_active_subscription`` is somewhere in the
   dependency tree, directly, via ``require_baa_acceptance``, or via
   a router-level ``dependencies=[...]``. The subscription's access
   level decides, and read/write intent decides which routes survive
   a read-only wind-down.
2. **Auth-posture-exempt** — the route is not a signed-in
   practitioner acting inside their practice: public routes, pre-MFA
   onboarding, service-account callbacks, platform admin. There is no
   subscription in scope to check.
3. **Explicitly exempt** — the route carries
   ``Depends(subscription_exempt)``, declaring at the route that it
   works in any subscription state.

There is no allowlist. A route that is none of the three fails the
test with instructions, in the diff at PR time.

The other two tests here defend the read/write classification rather
than the gate: intent overrides must point at routes that still
exist, and a gated ``GET`` that looks like an OAuth leg must be
override-classified as a write, because that is the shape of route
where "GET means read" is wrong and the mistake is invisible.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

# conftest.py mocks the DB engine + session factory and sets
# ENVIRONMENT=development before any app code is imported, so this
# import does not need a live Postgres.
from app.auth.route_access import (
    _INTENT_OVERRIDES,
    AccessIntent,
    access_intent,
    subscription_exempt,
)
from app.auth.route_security import truly_public
from app.auth.service import (
    get_current_user_no_mfa,
    get_session_peek_claims,
    require_active_subscription,
    require_admin,
    require_admin_hardware_key,
    require_cloud_tasks_invoker,
    require_pentest_runner,
)
from app.main import app
from app.route_introspection import iter_api_routes

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi.routing import APIRoute

# Marker dependencies that put a route outside the subscription gate
# by virtue of *who* is calling, not what the route does. None of these
# callers has a practice subscription in scope: anonymous traffic, a
# user who has not finished enrolling, a service account, or a platform
# administrator operating across practices.
AUTH_POSTURE_EXEMPT: tuple = (
    truly_public,
    get_current_user_no_mfa,
    get_session_peek_claims,
    require_pentest_runner,
    require_cloud_tasks_invoker,
    require_admin_hardware_key,
    require_admin,
)

# Path fragments that mark a route as a leg of a browser redirect
# (OAuth-style) flow. These arrive as GETs and routinely persist
# something, so they must be override-classified as writes.
_OAUTH_LIKE = re.compile(r"authorize|callback|oauth")


def _has_dependency(dependant, target_callables: tuple) -> bool:
    """True if any of ``target_callables`` appears anywhere in this tree."""
    if dependant.call in target_callables:
        return True
    return any(_has_dependency(sub, target_callables) for sub in dependant.dependencies)


def _is_gated(route: APIRoute) -> bool:
    return _has_dependency(route.dependant, (require_active_subscription,))


def _iter_api_routes() -> Iterator[tuple[str, APIRoute, str]]:
    """Yield ``(full_path, route, method)`` for every real path operation.

    ``HEAD`` is skipped where FastAPI synthesized it from a ``GET`` so a
    single endpoint counts once, matching ``test_route_mfa_guardrails``.
    """
    for path, route in iter_api_routes(app):
        for method in route.methods or ():
            if method == "HEAD":
                continue
            yield path, route, method


def test_every_route_declares_a_subscription_posture() -> None:
    """Catch the failure mode: a new route silently outside the gate.

    To fix a failure, pick the one that describes the route:

        ``user = Depends(require_baa_acceptance)`` — the route reads or
        records practice data, so the subscription's access level
        should decide. This is the default for anything patient-facing.

        ``_: None = Depends(subscription_exempt)`` — the route works in
        any subscription state (the caller's own profile, account
        status, agreements, audit trail; catalog data carrying no
        practice records).

        One of the auth-posture markers — the caller is not a signed-in
        practitioner acting inside a practice (public, pre-MFA,
        service account, platform admin), so no subscription applies.
    """
    unclassified: list[str] = []
    for path, route, method in _iter_api_routes():
        if _is_gated(route):
            continue
        if _has_dependency(route.dependant, AUTH_POSTURE_EXEMPT):
            continue
        if _has_dependency(route.dependant, (subscription_exempt,)):
            continue
        unclassified.append(f"  {method:6s} {path}  (handler: {route.endpoint.__qualname__})")

    assert not unclassified, (
        "Routes do not declare a subscription posture. Every route must be "
        "one of:\n"
        "  • gated — Depends(require_baa_acceptance) or "
        "Depends(require_active_subscription) (reads/records practice data; "
        "the access level decides)\n"
        "  • auth-posture-exempt — public, pre-MFA onboarding, "
        "service-account, or platform-admin (no subscription in scope)\n"
        "  • explicitly exempt — _: None = Depends(subscription_exempt) "
        "(works in any subscription state)\n\n"
        "Routes missing a posture:\n" + "\n".join(unclassified)
    )


def test_intent_overrides_reference_live_gated_routes() -> None:
    """An override for a route that moved is worse than no override.

    Overrides are keyed by ``(method, path_template)``. Rename the
    route and the key silently stops matching — the route falls back
    to its HTTP method, and a ``GET`` that writes quietly becomes a
    read again. Pin each key to a route that still exists, still
    accepts that method, and is still behind the gate (an override on
    an ungated route classifies nothing).
    """
    live: set[tuple[str, str]] = {
        (method, path) for path, route, method in _iter_api_routes() if _is_gated(route)
    }

    stale = sorted(key for key in _INTENT_OVERRIDES if key not in live)
    assert not stale, (
        "Read/write intent overrides name routes that are not live and gated. "
        "Update the keys in app.auth.route_access._INTENT_OVERRIDES to the "
        "current (method, path template), or drop the entry if the route is "
        "gone:\n  " + "\n  ".join(f"{m} {p}" for m, p in stale)
    )


def test_oauth_style_gated_gets_are_classified_as_writes() -> None:
    """Tripwire for the one place "GET means read" reliably lies.

    A redirect-flow leg has to be a GET — the browser navigates to
    it — and it typically persists what it was issued. Under read-only
    access that would be a write that survives the wind-down. This
    heuristic cannot catch every mis-classified GET, only the family
    that has burned people before; a GET that genuinely persists
    nothing is not exempt from thinking about it, it just isn't
    detectable from a path.
    """
    misclassified = sorted(
        f"  GET {path}"
        for path, route, method in _iter_api_routes()
        if method == "GET"
        and _is_gated(route)
        and _OAUTH_LIKE.search(path)
        and access_intent(method, path) is not AccessIntent.WRITE
    )

    assert not misclassified, (
        "Gated GET routes look like redirect-flow legs but are classified as "
        "reads. If the handler persists anything (tokens, state, a linked "
        "account), add it to app.auth.route_access._INTENT_OVERRIDES as "
        "AccessIntent.WRITE so a read-only subscription cannot drive it:\n"
        + "\n".join(misclassified)
    )


def test_subscription_posture_inventory_is_documented() -> None:
    """Snapshot the posture breakdown so a reviewer can see at a glance
    whether new routes shifted the mix.

    Always passes; run with ``-s`` to see the counts.
    """
    gated_read = gated_write = auth_exempt = explicit_exempt = 0
    for path, route, method in _iter_api_routes():
        if _is_gated(route):
            if access_intent(method, path) is AccessIntent.READ:
                gated_read += 1
            else:
                gated_write += 1
        elif _has_dependency(route.dependant, AUTH_POSTURE_EXEMPT):
            auth_exempt += 1
        elif _has_dependency(route.dependant, (subscription_exempt,)):
            explicit_exempt += 1

    print("\nRoute subscription-posture inventory:")
    print(f"  {'gated (read intent)':28s} {gated_read}")
    print(f"  {'gated (write intent)':28s} {gated_write}")
    print(f"  {'auth-posture-exempt':28s} {auth_exempt}")
    print(f"  {'subscription_exempt':28s} {explicit_exempt}")
    print(f"  {'intent overrides':28s} {len(_INTENT_OVERRIDES)}")
