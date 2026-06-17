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

from ..api_errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
)
from ..auth.providers import VerifiedIdentity
from ..auth.route_security import truly_public
from ..auth.service import get_current_user_no_mfa
from ..models.audit import AuditAction
from ..models.passkey import (
    PasskeyAuthenticationBegin,
    PasskeyAuthenticationResult,
    PasskeyAuthenticationVerify,
    PasskeyCredentialSummary,
    PasskeyRegistrationResult,
    PasskeyRegistrationVerify,
)
from ..models.user import User
from ..rate_limit import require_rate_limit
from ..repositories import UserRepository, get_user_repository
from ..services import AuditService, get_audit_service
from ..services.backup_code_service import BackupCodeService, get_backup_code_service
from ..services.passkey_service import (
    PasskeyAssertionError,
    PasskeyCeremonyError,
    PasskeyEnrollmentError,
    PasskeyLastHardwareKeyError,
    PasskeyService,
    get_passkey_service,
)
from ..settings import get_settings
from ..utcnow import utc_now

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
    request: Request,
    user: EnrollingUser,
    passkey_service: PasskeyService = Depends(get_passkey_service),
    user_repo: UserRepository = Depends(get_user_repository),
    audit: AuditService = Depends(get_audit_service),
    backup: BackupCodeService = Depends(get_backup_code_service),
) -> PasskeyRegistrationResult:
    """Verify the attestation response and persist the credential.

    A verified passkey is a phishing-resistant second factor, so the first
    one a user enrolls satisfies the second-factor milestone the onboarding
    wizard and the dashboard gate read (``mfa_enrolled_at``) — this is what
    lets onboarding be passkey-first with TOTP as a fallback. On that first
    enrolment we also issue one-time backup codes and return them once so the
    client can show them (Layer-1 recovery; see authentication-mfa-policy §6.4).

    The result also carries a ``custom_token`` minted from the verified
    attestation (``pablo_amr: ["webauthn"]``). The client exchanges it and
    force-refreshes its ID token so the freshly-enrolled session clears MFA
    in one ceremony — no sign-out/in before reaching PHI (PABLO-mee).
    """
    try:
        result = passkey_service.finish_registration(
            user_id=user.id,
            credential=payload.credential,
            device_label=payload.device_label,
        )
    except PasskeyCeremonyError as err:
        raise BadRequestError("Passkey registration could not be verified.") from err

    if user.mfa_enrolled_at is None:
        user.mfa_enrolled_at = utc_now()
        user_repo.update(user)
        audit.log_onboarding_milestone(
            AuditAction.ONBOARDING_MFA_ENROLLED, user, request, changes={"factor": "passkey"}
        )
        result = result.model_copy(update={"backup_codes": backup.issue(user.id)})
    return result


@router.get("/credentials", response_model=list[PasskeyCredentialSummary])
def list_credentials(
    user: EnrollingUser,
    passkey_service: PasskeyService = Depends(get_passkey_service),
) -> list[PasskeyCredentialSummary]:
    """List the current user's enrolled passkeys for the manage UI."""
    return passkey_service.list_credentials(user.id)


@router.delete("/credentials/{credential_id}", status_code=204)
def revoke_credential(
    credential_id: str,
    request: Request,
    user: EnrollingUser,
    passkey_service: PasskeyService = Depends(get_passkey_service),
) -> None:
    """Soft-revoke one of the current user's passkeys.

    Requires an MFA-satisfied session: removing a factor is a security
    downgrade, so a phished first-factor session must not be able to strip
    passkeys off an account (and re-enroll a rogue one).
    """
    if not _session_mfa_satisfied(request):
        raise ForbiddenError(
            "Verify a passkey before removing one", code="MFA_REQUIRED"
        )
    settings = get_settings()
    require_hardware_floor = settings.webauthn_admin_require_hardware_key and user.is_admin
    try:
        removed = passkey_service.revoke_credential(
            user_id=user.id,
            credential_id=credential_id,
            require_hardware_floor=require_hardware_floor,
        )
    except PasskeyLastHardwareKeyError as err:
        raise ConflictError(
            "Enroll a second hardware security key before removing this one",
            code="LAST_HARDWARE_KEY",
        ) from err
    if not removed:
        raise NotFoundError("Passkey not found")


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
