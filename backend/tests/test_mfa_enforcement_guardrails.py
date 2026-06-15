# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Guardrails for the MFA-enforcement seam (adversarial review Findings 4 & 7).

These lock two properties the passkey work depends on but didn't yet pin
with a test:

- **F4** — ``get_current_user_id`` resolves a user from a token but enforces
  **neither** ``require_mfa`` nor the idle gate. No PHI-reaching route may hang
  off it, or it is a clean first-factor-only bypass. This walks every route and
  fails if any depends on it.
- **F7** — ``mfa_satisfied`` must be set **once**, by the verifier, from token
  contents, and never upgraded afterward. ``VerifiedIdentity`` is frozen, and a
  token carrying no recognised second factor must never be ``mfa_satisfied``.
"""

from __future__ import annotations

import pytest
from app.auth.providers import FirebaseVerifier, VerifiedIdentity, second_factor_satisfied
from app.auth.service import get_current_user_id
from app.main import app
from fastapi.routing import APIRoute
from pydantic import ValidationError


def _has_dependency(dependant, target) -> bool:
    if dependant.call is target:
        return True
    return any(_has_dependency(sub, target) for sub in dependant.dependencies)


def test_no_route_depends_on_get_current_user_id() -> None:
    """No route may use the non-MFA/non-idle ``get_current_user_id`` (F4)."""
    offenders = [
        f"  {method:6s} {route.path}  (handler: {route.endpoint.__qualname__})"
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
        if method != "HEAD" and _has_dependency(route.dependant, get_current_user_id)
    ]
    assert not offenders, (
        "These routes depend on get_current_user_id, which enforces neither MFA "
        "nor the idle gate — a first-factor-only path to whatever they return. "
        "Use get_current_user / get_tenant_context (MFA-required) instead:\n"
        + "\n".join(offenders)
    )


def test_verified_identity_mfa_satisfied_is_immutable() -> None:
    """``mfa_satisfied`` is set at construction and can't be upgraded later (F7)."""
    identity = VerifiedIdentity(
        provider="firebase",
        subject_id="u",
        email="t@pablo.health",
        mfa_satisfied=False,
        claims={},
    )
    with pytest.raises(ValidationError):
        identity.mfa_satisfied = True  # frozen model — no post-verifier upgrade


def test_no_recognised_factor_is_never_mfa_satisfied() -> None:
    """A token with no second-factor signal must not satisfy MFA (F7)."""
    assert second_factor_satisfied({"uid": "u", "email": "t@pablo.health"}) is False
    identity = FirebaseVerifier().verify_from_decoded({"uid": "u"})
    assert identity.mfa_satisfied is False
