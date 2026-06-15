# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Pre-auth endpoints for native app code exchange (RFC 8252).

These endpoints do NOT require authentication — they run before the user has a JWT.
"""

import logging
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from firebase_admin import auth as firebase_auth
from pydantic import BaseModel

from ..api_errors import BadRequestError, ForbiddenError, UnauthorizedError
from ..auth.firebase_init import initialize_firebase_app
from ..auth.providers import second_factor_satisfied
from ..auth.route_security import truly_public
from ..models.companion_device import CompanionEnrollment
from ..rate_limit import require_rate_limit
from ..repositories import get_identity_repository
from ..services.auth_code_store import create_auth_code, exchange_auth_code
from ..services.companion_device_service import (
    InvalidDeviceJWKError,
    get_companion_device_service,
)
from ..settings import get_settings
from ..version_check import check_client_version

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# --- Native App Code Exchange (RFC 8252) ---

ALLOWED_NATIVE_SCHEMES = {"pablohealth", "therapyrecorder"}


def _is_valid_native_redirect_uri(uri: str) -> bool:
    """Validate that the redirect URI is an allowed native app callback."""
    try:
        parsed = urlparse(uri)
    except Exception:
        logger.debug("Failed to parse native redirect URI")
        return False
    if parsed.scheme in ALLOWED_NATIVE_SCHEMES:
        return True
    # Allow loopback for native apps (RFC 8252 Section 7.3)
    return parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1")


class CreateAuthCodeRequest(BaseModel):
    id_token: str
    refresh_token: str
    redirect_uri: str


class CreateAuthCodeResponse(BaseModel):
    code: str


class ExchangeAuthCodeRequest(BaseModel):
    code: str
    redirect_uri: str
    # Optional companion device enrollment payload. Present when the
    # native app is registering its install on first OAuth; absent for
    # legacy clients that pre-date the THERAPY-xo0o rollout. When
    # present we persist a row in platform.companion_devices and bind
    # the supplied JWK to the user — used by the DPoP middleware
    # (THERAPY-6qtr) to verify per-request proofs.
    enrollment: CompanionEnrollment | None = None


class ExchangeAuthCodeResponse(BaseModel):
    id_token: str
    refresh_token: str


@router.post("/native/code", response_model=CreateAuthCodeResponse)
def create_native_code(
    request: CreateAuthCodeRequest,
    http_request: Request,
    _: None = Depends(require_rate_limit),
    _public: None = Depends(truly_public),
) -> CreateAuthCodeResponse:
    """Generate a one-time authorization code for native app auth.

    The frontend calls this after Firebase authentication succeeds,
    passing the tokens. Returns an opaque code (60s TTL, single-use)
    that the native app exchanges for tokens via /native/exchange.

    The id_token is verified server-side before issuing a code to
    prevent storing arbitrary or forged payloads.
    """
    # Block outdated desktop clients before processing auth
    check_client_version(http_request)

    if not _is_valid_native_redirect_uri(request.redirect_uri):
        raise BadRequestError("Invalid redirect_uri.")

    # Verify the Firebase id_token before issuing a code.
    initialize_firebase_app()
    try:
        decoded_token = firebase_auth.verify_id_token(request.id_token, check_revoked=True)
    except Exception as err:
        logger.warning("Native code request with invalid Firebase JWT")
        logger.debug("Firebase JWT verify error detail: %s", err)
        raise UnauthorizedError("Invalid or expired id_token.") from err

    # This path issues the native auth code without going through require_mfa,
    # so it checks the second factor here directly, using the same
    # second_factor_satisfied() the verifier uses.
    settings = get_settings()
    if (
        settings.require_mfa
        and not settings.is_development
        and not second_factor_satisfied(decoded_token)
    ):
        raise ForbiddenError("Multi-factor authentication is required", code="MFA_REQUIRED")

    code = create_auth_code(
        id_token=request.id_token,
        refresh_token=request.refresh_token,
        redirect_uri=request.redirect_uri,
        firebase_uid=decoded_token.get("uid"),
    )
    return CreateAuthCodeResponse(code=code)


@router.post("/native/exchange", response_model=ExchangeAuthCodeResponse)
def exchange_native_code(
    request: ExchangeAuthCodeRequest,
    _: None = Depends(require_rate_limit),
    _public: None = Depends(truly_public),
) -> ExchangeAuthCodeResponse:
    """Exchange a one-time authorization code for tokens.

    Called by the native app after receiving the code via redirect.
    Codes are single-use and expire after 60 seconds.

    If the native app includes an ``enrollment`` payload (companion
    install_id + Secure-Enclave / TPM public key), this is also the
    enrollment point: we persist a ``companion_devices`` row keyed to
    the authenticated user. See THERAPY-xo0o.
    """
    entry = exchange_auth_code(request.code)
    if entry is None:
        raise BadRequestError("Invalid or expired authorization code.")
    # Validate redirect_uri matches what was bound at code creation
    if entry.redirect_uri != request.redirect_uri:
        raise BadRequestError("redirect_uri mismatch.")

    if request.enrollment is not None:
        _enroll_companion_device(entry.firebase_uid, request.enrollment)

    return ExchangeAuthCodeResponse(
        id_token=entry.id_token,
        refresh_token=entry.refresh_token,
    )


def _enroll_companion_device(firebase_uid: str | None, enrollment: CompanionEnrollment) -> None:
    """Persist a companion device row, mapping firebase_uid → pablo user_id.

    Failures here do NOT block the token exchange — a stale or invalid
    payload should not prevent the user from getting their tokens.
    The companion will retry enrollment on next launch when it sees
    that DPoP-protected endpoints are rejecting it (THERAPY-6qtr).
    """
    if firebase_uid is None:
        # Legacy in-flight code (pre-deploy) — no uid stashed; skip.
        logger.info("companion_enrollment_skipped reason=missing_firebase_uid")
        return
    try:
        pablo_user_id = get_identity_repository().resolve_or_create("firebase", firebase_uid)
        get_companion_device_service().enroll(pablo_user_id, enrollment)
    except InvalidDeviceJWKError as err:
        logger.warning(
            "companion_enrollment_rejected reason=invalid_jwk install_id=%s detail=%s",
            enrollment.install_id,
            err,
        )
    except Exception:
        logger.exception("companion_enrollment_failed install_id=%s", enrollment.install_id)
