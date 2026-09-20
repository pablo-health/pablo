# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Signed portal tokens (PyJWT, HS256).

Two token types, both scoped to a single patient AND a single practice:

* **invite** — carried in the magic link the clinician sends. Possession of
  this is ONE factor; redemption additionally requires the texted code (the
  step-up). Short TTL (~15 min), single-use, enforced by the server-side
  challenge keyed on ``jti`` (see :mod:`app.portal.store`).
* **session** — minted on successful redemption; the patient's bearer
  credential for every patient-facing route. Refreshable, ~1 h TTL.

The ``typ`` claim is validated on every verify, so an invite token can never
be replayed as a session token or the reverse. Tokens carry no PHI — only
opaque ids.

The two ``typ`` values are part of the token format as it exists on the
wire. A token already in someone's inbox was signed with the value below, so
these strings are stable identifiers rather than descriptive names: changing
one invalidates every credential issued before the change.
"""

from __future__ import annotations

from dataclasses import dataclass

import jwt

from .errors import ExpiredInviteError, InvalidInviteError, InvalidSessionError

_ALGORITHM = "HS256"
_INVITE_TYP = "companion_invite"
_SESSION_TYP = "companion_session"


@dataclass(frozen=True)
class InviteClaims:
    jti: str
    patient_id: str
    tenant: str
    purpose: str


@dataclass(frozen=True)
class SessionClaims:
    """A minted session's claims.

    ``jti`` is what makes a session individually revocable: it names the
    server-side row the resolver checks on every request, so a clinician's
    revoke takes effect immediately instead of waiting out the token's TTL.
    Refresh rotates it (new row, old row retired), which is also how a
    stolen token stops working once the real patient renews.
    """

    jti: str
    patient_id: str
    tenant: str


@dataclass(frozen=True)
class TokenLifetime:
    """When a token was issued and how long it lives (unix seconds). Passed
    in so callers control the clock and tests stay deterministic."""

    issued_at: int
    ttl_seconds: int

    @property
    def expires_at(self) -> int:
        return self.issued_at + self.ttl_seconds


def mint_invite_token(*, signing_key: str, claims: InviteClaims, lifetime: TokenLifetime) -> str:
    """Sign a single-use invite token from its claims + lifetime."""
    payload = {
        "typ": _INVITE_TYP,
        "jti": claims.jti,
        "pid": claims.patient_id,
        "tid": claims.tenant,
        "purpose": claims.purpose,
        "iat": lifetime.issued_at,
        "exp": lifetime.expires_at,
    }
    return jwt.encode(payload, signing_key, algorithm=_ALGORITHM)


def verify_invite_token(*, signing_key: str, token: str) -> InviteClaims:
    """Decode + validate an invite token.

    Raises ``ExpiredInviteError`` if past its ``exp``; ``InvalidInviteError``
    for tamper, wrong type or malformed input.
    """
    try:
        payload = jwt.decode(token, signing_key, algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise ExpiredInviteError(str(exc)) from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidInviteError(str(exc)) from exc

    if payload.get("typ") != _INVITE_TYP:
        raise InvalidInviteError(f"wrong token type: {payload.get('typ')!r}")
    try:
        return InviteClaims(
            jti=str(payload["jti"]),
            patient_id=str(payload["pid"]),
            tenant=str(payload["tid"]),
            purpose=str(payload["purpose"]),
        )
    except KeyError as exc:
        raise InvalidInviteError(f"missing claim: {exc}") from exc


def mint_session_token(*, signing_key: str, claims: SessionClaims, lifetime: TokenLifetime) -> str:
    """Sign a patient-session token from its claims + lifetime."""
    payload = {
        "typ": _SESSION_TYP,
        "jti": claims.jti,
        "sub": claims.patient_id,
        "tid": claims.tenant,
        "scope": "patient",
        "iat": lifetime.issued_at,
        "exp": lifetime.expires_at,
    }
    return jwt.encode(payload, signing_key, algorithm=_ALGORITHM)


def verify_session_token(*, signing_key: str, token: str) -> SessionClaims:
    """Decode + validate a patient-session token.

    Raises ``InvalidSessionError`` for expired, tampered, wrong-type or
    malformed input — one error for all of them, because the caller's answer
    is the same in every case.
    """
    try:
        payload = jwt.decode(token, signing_key, algorithms=[_ALGORITHM])
    except jwt.InvalidTokenError as exc:
        # Expired included — a patient simply re-redeems or refreshes.
        raise InvalidSessionError(str(exc)) from exc

    if payload.get("typ") != _SESSION_TYP:
        raise InvalidSessionError(f"wrong token type: {payload.get('typ')!r}")
    try:
        return SessionClaims(
            jti=str(payload["jti"]),
            patient_id=str(payload["sub"]),
            tenant=str(payload["tid"]),
        )
    except KeyError as exc:
        raise InvalidSessionError(f"missing claim: {exc}") from exc
