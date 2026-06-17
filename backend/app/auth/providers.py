# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Pluggable OIDC token-verification backends.

Pablo verifies bearer ID tokens at a single chokepoint, but the token
may be signed by more than one issuer. This module defines the seam:

- ``VerifiedIdentity`` — the provider-agnostic result every verifier
  returns. Downstream code (``auth/service.py``) resolves it to Pablo's
  internal ``user_id`` via the ``platform.user_identities`` mapping,
  keyed on ``(provider, subject_id)``, so the auth backend that minted
  the token never leaks into storage identity.
- ``TokenVerifier`` — a structural protocol: an ``issuer`` string and a
  ``verify(token)`` method.
- ``FirebaseVerifier`` — wraps the existing Firebase verification path
  unchanged and normalizes its decoded claims.
- ``OidcVerifier`` — a generic OIDC backend (RS256 + JWKS) for any
  standards-compliant issuer (e.g. Keycloak). Configured per deployment;
  inert unless the issuer is set.
- ``VerifierRegistry`` — dispatches on the token's ``iss`` claim.

When no OIDC issuer is configured the registry holds Firebase only, and
every code path behaves byte-for-byte as it did before this seam existed.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

import jwt
from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

# Firebase's issuer is project-scoped: https://securetoken.google.com/<project-id>.
# We never dispatch on the Firebase issuer string (Firebase is the default /
# fallback verifier), so we don't need it here — only OIDC issuers are matched
# exactly against their configured ``iss``.

_RS256_ONLY = ["RS256"]


class VerifiedIdentity(BaseModel):
    """Normalized result of verifying a bearer ID token.

    ``provider`` + ``subject_id`` is the natural key into
    ``platform.user_identities``. ``mfa_satisfied`` is the issuer's
    assertion that a second factor was used — interpreted per provider
    (see each verifier). ``claims`` is the full decoded payload for
    callers that still need raw claims (email extraction, idle-session,
    legacy ``firebase.*`` reads).
    """

    model_config = ConfigDict(frozen=True)

    provider: str
    subject_id: str
    email: str
    mfa_satisfied: bool
    claims: dict[str, Any]


@runtime_checkable
class TokenVerifier(Protocol):
    """A backend that verifies a bearer token for one issuer."""

    issuer: str

    def verify(self, token: str) -> VerifiedIdentity:
        raise NotImplementedError


class FirebaseVerifier:
    """Verifies Firebase ID tokens via the existing Firebase path.

    Delegates to ``auth.service.verify_firebase_token`` (which keeps the
    revocation check, the typed ``HTTPException`` mapping, and the
    ``initialize_firebase_app`` call) and maps the decoded claims into a
    ``VerifiedIdentity``.

    ``mfa_satisfied`` mirrors exactly what ``require_mfa`` asserts for
    Firebase today: the presence of the ``firebase.sign_in_second_factor``
    claim. Nothing else about the MFA decision (the require_mfa /
    development / iap / E2E gates) lives here — those stay in
    ``require_mfa`` so behavior is unchanged.
    """

    # Firebase is the default verifier; it is selected as a fallback rather
    # than matched on a literal issuer, so this sentinel is never compared.
    issuer = "firebase"
    provider = "firebase"

    def verify(self, token: str) -> VerifiedIdentity:
        # Imported lazily to avoid a circular import: service.py imports
        # this module to build its registry.
        from .service import verify_firebase_token

        decoded = verify_firebase_token(token)
        return self.verify_from_decoded(decoded)

    def verify_from_decoded(self, decoded: dict[str, Any]) -> VerifiedIdentity:
        """Map already-verified Firebase claims into a VerifiedIdentity.

        Used both by ``verify`` and by the dependency chain when the
        DatabaseSessionMiddleware has already verified and cached the
        Firebase token on ``request.state`` (avoiding a second round-trip).
        """
        uid = decoded.get("uid")
        if not uid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "INVALID_TOKEN",
                        "message": "User ID not found in token",
                        "details": {},
                    }
                },
            )
        # A passkey session arrives via a Firebase custom token, which can't
        # carry the reserved sign_in_second_factor claim, so the WebAuthn factor
        # is read from our pablo_amr claim instead (see second_factor_satisfied).
        mfa_satisfied = second_factor_satisfied(decoded)
        return VerifiedIdentity(
            provider=self.provider,
            subject_id=str(uid),
            email=_extract_email_claim(decoded),
            mfa_satisfied=mfa_satisfied,
            claims=decoded,
        )


class OidcVerifier:
    """Verifies RS256-signed OIDC ID tokens from a configured issuer.

    Uses PyJWT's ``PyJWKClient`` to fetch (and cache) the issuer's signing
    keys from its JWKS endpoint, then validates the token with the
    signature algorithm restricted to RS256. Restricting the algorithm is
    a deliberate alg-confusion guard: it rejects ``none`` and any HMAC
    (``HS*``) token that would otherwise be verifiable with the public
    JWKS key as if it were a shared secret.

    Claims enforced: signature (RS256), ``aud == audience``,
    ``iss == issuer``, ``exp``/``nbf`` (PyJWT enforces these by default
    once present). ``subject_id`` comes from ``sub``; ``email`` from the
    standard flat ``email`` claim.

    ``mfa_satisfied`` is derived from the OIDC AMR/ACR step-up signals:
    ``amr`` (RFC 8176 authentication-methods-reference) containing an
    ``mfa`` or ``otp`` entry, or an ``acr`` of ``mfa``. A Keycloak realm
    that enforces a second factor reflects it there. If neither claim is
    present we default to ``False`` (no proof of a second factor).
    """

    provider = "oidc"

    def __init__(self, issuer: str, audience: str, jwks_uri: str) -> None:
        self.issuer = issuer
        self._audience = audience
        self._jwks_uri = jwks_uri
        # PyJWKClient caches fetched keys internally (per kid), so we keep a
        # single long-lived client rather than re-fetching JWKS per request.
        self._jwk_client = jwt.PyJWKClient(jwks_uri)

    def verify(self, token: str) -> VerifiedIdentity:
        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(token)
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=_RS256_ONLY,
                audience=self._audience,
                issuer=self.issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as err:
            # No PHI / token material in logs — failure reason only.
            # nosemgrep
            logger.warning("OIDC ID token rejected: %s", err)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "INVALID_TOKEN",
                        "message": "Invalid authentication token",
                        "details": {},
                    }
                },
            ) from err

        subject_id = claims.get("sub")
        if not subject_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "INVALID_TOKEN",
                        "message": "User ID not found in token",
                        "details": {},
                    }
                },
            )

        return VerifiedIdentity(
            provider=self.provider,
            subject_id=str(subject_id),
            email=str(claims.get("email", "")).lower(),
            mfa_satisfied=second_factor_satisfied(claims),
            claims=claims,
        )


def passkey_factor_satisfied(claims: dict[str, Any]) -> bool:
    """True if the token carries our server-minted WebAuthn factor claim.

    ``pablo_amr`` is set by ``create_custom_token`` in the passkey mint
    endpoint and only there, after a verified assertion. It appears at the
    top level of the Firebase ID token and the client can never set it.
    """
    amr = claims.get("pablo_amr")
    return isinstance(amr, list) and "webauthn" in amr


def _recovery_factor_satisfied(claims: dict[str, Any]) -> bool:
    """True if the token carries our server-minted recovery-code factor claim.

    Set by the recovery-code redemption endpoint only, after the user has
    proven a first factor and spent a valid single-use backup code — so a
    backup code is always a *second* factor (first factor + code), never a
    standalone login. Like ``pablo_amr: ["webauthn"]``, the client can't set it.
    """
    amr = claims.get("pablo_amr")
    return isinstance(amr, list) and "recovery" in amr


def _oidc_mfa_satisfied(claims: dict[str, Any]) -> bool:
    """Derive MFA satisfaction from OIDC AMR/ACR claims.

    Keys on RFC 8176 ``amr`` entries (``mfa``/``otp``) or an ``acr`` of
    ``mfa``. Absent any such signal, returns False.
    """
    amr = claims.get("amr")
    if isinstance(amr, list) and any(m in ("mfa", "otp") for m in amr):
        return True
    return claims.get("acr") == "mfa"


def second_factor_satisfied(claims: dict[str, Any]) -> bool:
    """Whether a token presents a second factor, by any supported method.

    Recognises a Firebase-native second factor (TOTP/SMS), our WebAuthn
    ``pablo_amr`` claim, or an OIDC AMR/ACR step-up. Used by both verifiers
    and the native code-exchange gate so they share one definition.
    """
    # ``firebase.sign_in_second_factor`` is a Firebase-reserved claim, set to
    # the factor type ("phone"/"totp") only on an MFA sign-in and absent
    # otherwise; the client cannot set it, so its presence is the test.
    firebase_claims = claims.get("firebase", {})
    if firebase_claims.get("sign_in_second_factor"):
        return True
    if passkey_factor_satisfied(claims):
        return True
    if _recovery_factor_satisfied(claims):
        return True
    return _oidc_mfa_satisfied(claims)


def _extract_email_claim(decoded: dict[str, Any]) -> str:
    """Read an email from a decoded token (flat claim, Firebase fallback).

    Kept independent of ``service._extract_email`` to avoid a circular
    import; same precedence (flat ``email`` then ``firebase.identities``).
    """
    email = decoded.get("email", "")
    if not email:
        firebase_claims = decoded.get("firebase", {})
        identities = firebase_claims.get("identities", {})
        email_list = identities.get("email", [])
        if email_list:
            email = email_list[0]
    return email.lower() if email else ""


class VerifierRegistry:
    """Verifies a token by trying each backend in order.

    Firebase is always tried first; the OIDC backend is appended only
    when configured. With no OIDC backend the registry behaves exactly
    like calling Firebase directly.
    """

    def __init__(self, firebase: FirebaseVerifier, oidc: OidcVerifier | None) -> None:
        self._verifiers: list[TokenVerifier] = [firebase]
        if oidc is not None:
            self._verifiers.append(oidc)

    def verify(self, token: str) -> VerifiedIdentity:
        """Return the identity from the first verifier that accepts the token.

        Each verifier fully checks the signature and issuer; a 401 means
        "not my token" and falls through to the next backend, while any
        other error propagates immediately. The first 401 is re-raised
        when no verifier accepts the token, so the dominant Firebase
        backend's specific error codes (TOKEN_EXPIRED, TOKEN_REVOKED)
        reach the client rather than a later backend's generic 401.
        """
        first_exc: HTTPException | None = None
        for verifier in self._verifiers:
            try:
                return verifier.verify(token)
            except HTTPException as exc:
                if exc.status_code != status.HTTP_401_UNAUTHORIZED:
                    raise
                if first_exc is None:
                    first_exc = exc
        raise first_exc or HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
