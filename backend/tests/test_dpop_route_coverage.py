# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Fail-closed route coverage for the DPoP middleware (stage 2).

Companion design: ``docs/design/companion-dpop-binding.md`` § "Test
enforcement — impossible to forget the header".

The DPoP middleware (``app.middleware.dpop.DPoPMiddleware``) is **global**
— it wraps every request, and when ``ENABLE_DPOP_VALIDATION`` is on it
enforces a proof on any request that carries an ``X-Install-ID`` bound to
an authenticated user. So every *authenticated* route is automatically
DPoP-covered: there is no per-route decorator to forget.

The failure mode this test guards is the inverse: a new route that is
**truly public** can never be DPoP-bound (there is no authenticated user
to look up an ``install_id`` against). Those routes are the only ones the
middleware structurally cannot protect, so each one must be an explicit,
justified entry in ``DPOP_UNCOVERABLE`` below. A new public route that
nobody thought about fails this test, pointing the author at the design
doc to make a deliberate decision.

Mechanism: reuse the four security-posture markers from
``test_route_mfa_guardrails`` to classify every route. A route is
DPoP-coverable as soon as its bearer token resolves to a user_id — which
is true for both fully-authenticated (``mfa-required``) routes AND
pre-MFA onboarding routes (``get_current_user_no_mfa`` still resolves an
authenticated user; the device just isn't enrolled until after the OAuth
exchange). Service-account routes authenticate via Cloud Tasks-style ID
tokens, never a companion, so a proof is not applicable. The ONLY posture
with no user for the middleware to bind a device to is ``truly-public``;
every such route MUST appear in the allow-list with a justification.
"""

from __future__ import annotations

from app.auth.route_security import truly_public
from app.auth.service import (
    get_current_user_no_mfa,
    require_mfa,
    require_pentest_runner,
)
from app.main import app
from fastapi.routing import APIRoute

# Postures, in classification order. "mfa-required" and
# "pre-mfa-onboarding" both resolve the bearer token to an authenticated
# user_id, so the global DPoP middleware can bind a device proof to them →
# covered. "service-account-auth" routes authenticate via Cloud Tasks-style
# ID tokens, never a companion, so a proof is not applicable. Only
# "truly-public" has no user for the middleware to bind a device to.
_MARKERS = {
    "mfa-required": require_mfa,
    "pre-mfa-onboarding": get_current_user_no_mfa,
    "service-account-auth": require_pentest_runner,
    "truly-public": truly_public,
}

# Postures the DPoP middleware can enforce against (the request resolves to
# an authenticated user the device is bound to) OR for which a proof is not
# applicable (service-account callers are never companions).
_DPOP_COVERED_POSTURES = {"mfa-required", "pre-mfa-onboarding", "service-account-auth"}

# The only posture with no authenticated user. Every route here MUST be
# allow-listed below with a justification.
_UNCOVERABLE_POSTURES = {"truly-public"}

# ---------------------------------------------------------------------------
# Explicit allow-list: routes the DPoP middleware cannot bind a device to.
#
# Each key is "<METHOD> <path-template>"; each value is the human-readable
# justification. Adding a public/pre-auth route WITHOUT an entry here fails
# ``test_public_routes_are_explicitly_allowlisted``.
#
# Justifications map to the exemption classes in
# docs/design/companion-dpop-binding.md § "Middleware-by-default":
#   • pre-auth — no authenticated user yet, no install_id to check
#   • health/version — liveness probes, no PHI, no user
#   • webhook — authenticated by shared-secret signature, not a user JWT
# ---------------------------------------------------------------------------
DPOP_UNCOVERABLE: dict[str, str] = {
    # --- Pre-auth native OAuth handoff. The companion has no enrolled
    # device yet (the exchange endpoint is where it enrolls one), so there
    # is no install_id to bind a proof to. ---
    "POST /api/auth/native/code": "pre-auth: mints the native OAuth handoff code",
    "POST /api/auth/native/exchange": "pre-auth: exchanges the code + enrolls the device",
    # --- Pre-auth allowlist / status probes used by the browser extension
    # and sign-in screen before a session exists. No authenticated user. ---
    "POST /api/ext/auth/check-allowlist": "pre-auth: checks email allowlist before sign-in",
    "POST /api/ext/auth/check-status": "pre-auth: checks account status before sign-in",
    # --- Public BAA document fetch (the legal text shown pre-acceptance);
    # no authenticated user. ---
    "GET /api/users/baa": "public: serves the current BAA document text, no user",
    # --- Liveness probe: no user, no PHI. ---
    "GET /api/health": "health probe: liveness only, no authenticated user",
}


def _classify(route: APIRoute) -> str | None:
    def has(dep, target) -> bool:
        if dep.call is target:
            return True
        return any(has(sub, target) for sub in dep.dependencies)

    for name, marker in _MARKERS.items():
        if has(route.dependant, marker):
            return name
    return None


def _iter_routes():
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            if method == "HEAD":
                continue
            yield route, method


def _key(method: str, path: str) -> str:
    return f"{method} {path}"


def test_public_routes_are_explicitly_allowlisted() -> None:
    """Every route the DPoP middleware cannot cover must be allow-listed.

    A route with a "truly-public" or "pre-mfa-onboarding" posture has no
    fully-authenticated companion user, so the middleware can never bind a
    device proof to it. Each such route must be a deliberate, justified
    entry in ``DPOP_UNCOVERABLE``. A new one that isn't fails here.
    """
    missing: list[str] = []
    for route, method in _iter_routes():
        posture = _classify(route)
        if posture in _UNCOVERABLE_POSTURES:
            key = _key(method, route.path)
            if key not in DPOP_UNCOVERABLE:
                handler = route.endpoint.__qualname__
                missing.append(f"  {key}  (handler: {handler}, posture: {posture})")

    assert not missing, (
        "Public / pre-auth routes are not in the DPoP allow-list. The DPoP "
        "middleware cannot bind a device proof to a route with no "
        "fully-authenticated user, so each such route must be an explicit, "
        "justified entry in DPOP_UNCOVERABLE (see "
        "docs/design/companion-dpop-binding.md § 'Test enforcement'). If the "
        "route SHOULD require auth, give it Depends(get_current_user); if it "
        "is genuinely public, add it to DPOP_UNCOVERABLE with a justification.\n\n"
        "Routes missing an allow-list entry:\n" + "\n".join(missing)
    )


def test_allowlist_has_no_stale_entries() -> None:
    """Keep the allow-list honest: no entry for a route that no longer
    exists, and no entry for a route that is actually authenticated (which
    would mean the middleware DOES cover it and the exemption is wrong)."""
    live: dict[str, str | None] = {}
    for route, method in _iter_routes():
        live[_key(method, route.path)] = _classify(route)

    stale = [k for k in DPOP_UNCOVERABLE if k not in live]
    assert not stale, (
        "DPOP_UNCOVERABLE lists routes that no longer exist on the app. "
        "Remove the stale entries:\n  " + "\n  ".join(sorted(stale))
    )

    wrongly_listed = [
        k for k in DPOP_UNCOVERABLE if live.get(k) in _DPOP_COVERED_POSTURES
    ]
    assert not wrongly_listed, (
        "DPOP_UNCOVERABLE lists routes that ARE authenticated (and therefore "
        "already DPoP-covered by the global middleware). Drop these entries:\n  "
        + "\n  ".join(sorted(wrongly_listed))
    )


def test_every_route_is_either_covered_or_allowlisted() -> None:
    """Belt-and-braces: every route is accounted for — either DPoP-covered
    (authenticated / service-account) or explicitly allow-listed."""
    unaccounted: list[str] = []
    for route, method in _iter_routes():
        posture = _classify(route)
        key = _key(method, route.path)
        if posture in _DPOP_COVERED_POSTURES:
            continue
        if key in DPOP_UNCOVERABLE:
            continue
        unaccounted.append(f"  {key}  (posture: {posture})")

    assert not unaccounted, (
        "Routes are neither DPoP-covered nor allow-listed. See "
        "docs/design/companion-dpop-binding.md § 'Test enforcement':\n"
        + "\n".join(unaccounted)
    )
