# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Typed failures for the portal sign-in flow.

Each names a distinct way redemption or rotation can fail, so the service
layer can be precise while the route layer stays uniform: every one of
these becomes the same 401 on the wire. The messages here are for logs and
tests, never for the patient's response body — telling a caller *which*
refusal it hit turns the redeem endpoint into an oracle for which invites
exist and which codes are close.
"""

from __future__ import annotations


class PortalAuthError(Exception):
    """Base for every portal sign-in failure."""


class InvalidInviteError(PortalAuthError):
    """Token is malformed, tampered, the wrong type, or unknown."""


class ExpiredInviteError(PortalAuthError):
    """Token (or its server-side challenge) is past its TTL."""


class InviteAlreadyRedeemedError(PortalAuthError):
    """Single-use invite has already minted a session."""


class InvalidStepUpError(PortalAuthError):
    """The supplied step-up factor (the texted code) did not match."""


class TooManyAttemptsError(PortalAuthError):
    """Step-up attempts on this invite exceeded the cap."""


class InvalidSessionError(PortalAuthError):
    """Patient-session token is malformed, tampered, expired, or wrong type."""


class SessionRevokedError(PortalAuthError):
    """The session's server-side row is revoked (or gone) — the clinician
    kill switch, or a jti already rotated away by a refresh."""


class SessionLifetimeExceededError(PortalAuthError):
    """Sliding renewal hit its ceiling: this chain has been rotating since
    a redemption too far in the past. The patient redeems a fresh invite."""
