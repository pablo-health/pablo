# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""WebAuthn passkey ceremony endpoints (epic PABLO-egm, slice PABLO-egm.1).

- ``register/begin|finish`` enroll an authenticator on the current user
  (posture ``get_current_user_no_mfa``). The first passkey may be enrolled
  from a first-factor session; a later one needs an MFA-satisfied session.
  Enrolling does not grant PHI access.
- ``authenticate/begin|finish`` assert a passkey (posture ``truly_public`` +
  rate limit). ``finish`` mints the custom token that carries ``pablo_amr``.

No PHI — authenticator metadata and a user label only.
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

# Module-level alias so FastAPI resolves ``User`` at runtime.
EnrollingUser = Annotated[User, Depends(get_current_user_no_mfa)]


def _session_mfa_satisfied(request: Request) -> bool:
    """Whether the current session already cleared MFA.

    ``get_current_user_no_mfa`` stashes the VerifiedIdentity on
    ``request.state``; its ``mfa_satisfied`` covers passkey and TOTP.
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
            "Verify an existing passkey before adding another", code="MFA_REQUIRED"
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

    ``pablo_amr: ["webauthn"]`` is stamped on the token only here, after a
    verified assertion.
    """
    try:
        return passkey_service.finish_authentication(credential=payload.credential)
    except PasskeyCeremonyError as err:
        raise BadRequestError("Passkey assertion could not be verified.") from err
    except PasskeyAssertionError as err:
        raise UnauthorizedError("Passkey authentication failed.") from err
