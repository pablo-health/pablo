# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""WebAuthn passkey ceremony endpoints (epic PABLO-egm, slice PABLO-egm.1).

Four begin/finish halves:

- ``register/begin|finish`` — enrol an authenticator on an existing
  identity. Posture: ``get_current_user_no_mfa`` (pre-MFA onboarding);
  the first passkey may be enrolled from a first-factor session, a
  subsequent one needs an already-MFA-satisfied session (step-up).
  Enrolling grants NO PHI access — the user still has to assert.
- ``authenticate/begin|finish`` — assert a passkey. Posture:
  ``truly_public`` + rate limit; ``finish`` is a pre-auth, token-issuing
  surface, so it is the most security-sensitive route here. The custom
  token it mints is the ONLY place ``pablo_amr`` is stamped.

No PHI: authenticator metadata and a user label only — these routes are
classified non-PHI in ``check_route_audit.py``.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from ..api_errors import BadRequestError, ForbiddenError, UnauthorizedError
from ..auth.providers import VerifiedIdentity
from ..auth.route_security import truly_public
from ..auth.service import get_current_user_no_mfa
from ..models.passkey import (
    PasskeyAuthenticationBegin,
    PasskeyAuthenticationResult,
    PasskeyAuthenticationVerify,
    PasskeyRegistrationResult,
    PasskeyRegistrationVerify,
)
from ..models.user import User
from ..rate_limit import require_rate_limit
from ..services.passkey_service import (
    PasskeyAssertionError,
    PasskeyCeremonyError,
    PasskeyEnrollmentError,
    PasskeyService,
    get_passkey_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth/passkey", tags=["auth", "passkey"])

# Module-level alias so ``User`` is referenced at runtime (FastAPI evaluates
# the annotation; the alias also keeps the import out of a typing-only use).
EnrollingUser = Annotated[User, Depends(get_current_user_no_mfa)]


def _session_mfa_satisfied(request: Request) -> bool:
    """Whether the current first-factor session already cleared MFA.

    ``get_current_user_no_mfa`` stashes the VerifiedIdentity on
    ``request.state``; its ``mfa_satisfied`` already reflects passkey
    (``pablo_amr``) and legacy TOTP via the single provider seam.
    """
    identity = getattr(request.state, "verified_identity", None)
    return isinstance(identity, VerifiedIdentity) and identity.mfa_satisfied


@router.post("/register/begin")
def register_begin(
    request: Request,
    user: EnrollingUser,
    passkey_service: PasskeyService = Depends(get_passkey_service),
) -> dict[str, Any]:
    """Return WebAuthn registration options and store the ceremony challenge."""
    try:
        return passkey_service.begin_registration(
            user_id=user.id,
            account_email=user.email,
            session_mfa_satisfied=_session_mfa_satisfied(request),
        )
    except PasskeyEnrollmentError as err:
        raise ForbiddenError(
            "Verify an existing passkey before enrolling another", code="MFA_REQUIRED"
        ) from err


@router.post("/register/finish", response_model=PasskeyRegistrationResult, status_code=201)
def register_finish(
    payload: PasskeyRegistrationVerify,
    user: EnrollingUser,
    passkey_service: PasskeyService = Depends(get_passkey_service),
) -> PasskeyRegistrationResult:
    """Verify the attestation response and persist the credential."""
    try:
        return passkey_service.finish_registration(
            user_id=user.id,
            credential=payload.credential,
            device_label=payload.device_label,
        )
    except PasskeyCeremonyError as err:
        raise BadRequestError("Passkey registration could not be verified.") from err


@router.post("/authenticate/begin")
def authenticate_begin(
    _payload: PasskeyAuthenticationBegin,
    passkey_service: PasskeyService = Depends(get_passkey_service),
    _: None = Depends(require_rate_limit),
    _public: None = Depends(truly_public),
) -> dict[str, Any]:
    """Return usernameless WebAuthn authentication options + store the challenge."""
    return passkey_service.begin_authentication()


@router.post("/authenticate/finish", response_model=PasskeyAuthenticationResult)
def authenticate_finish(
    payload: PasskeyAuthenticationVerify,
    passkey_service: PasskeyService = Depends(get_passkey_service),
    _: None = Depends(require_rate_limit),
    _public: None = Depends(truly_public),
) -> PasskeyAuthenticationResult:
    """Verify the assertion and mint the passkey-factor custom token.

    Pre-auth, token-issuing surface. ``pablo_amr: ["webauthn"]`` is
    stamped on the minted token ONLY here, only after a fresh, verified
    assertion.
    """
    try:
        return passkey_service.finish_authentication(credential=payload.credential)
    except PasskeyCeremonyError as err:
        raise BadRequestError("Passkey assertion could not be verified.") from err
    except PasskeyAssertionError as err:
        raise UnauthorizedError("Passkey authentication failed.") from err
