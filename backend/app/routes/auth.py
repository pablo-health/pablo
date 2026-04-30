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
from ..rate_limit import require_rate_limit
from ..services.auth_code_store import create_auth_code, exchange_auth_code
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


class ExchangeAuthCodeResponse(BaseModel):
    id_token: str
    refresh_token: str


@router.post("/native/code", response_model=CreateAuthCodeResponse)
def create_native_code(
    request: CreateAuthCodeRequest,
    http_request: Request,
    _: None = Depends(require_rate_limit),
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

    # Enforce MFA: reject tokens without a completed second factor
    settings = get_settings()
    if settings.require_mfa and not settings.is_development and settings.auth_mode != "iap":
        firebase_claims = decoded_token.get("firebase", {})
        if not firebase_claims.get("sign_in_second_factor"):
            raise ForbiddenError(
                "Multi-factor authentication is required", code="MFA_REQUIRED"
            )

    code = create_auth_code(
        id_token=request.id_token,
        refresh_token=request.refresh_token,
        redirect_uri=request.redirect_uri,
    )
    return CreateAuthCodeResponse(code=code)


@router.post("/native/exchange", response_model=ExchangeAuthCodeResponse)
def exchange_native_code(
    request: ExchangeAuthCodeRequest, _: None = Depends(require_rate_limit)
) -> ExchangeAuthCodeResponse:
    """Exchange a one-time authorization code for tokens.

    Called by the native app after receiving the code via redirect.
    Codes are single-use and expire after 60 seconds.
    """
    entry = exchange_auth_code(request.code)
    if entry is None:
        raise BadRequestError("Invalid or expired authorization code.")
    # Validate redirect_uri matches what was bound at code creation
    if entry.redirect_uri != request.redirect_uri:
        raise BadRequestError("redirect_uri mismatch.")
    return ExchangeAuthCodeResponse(
        id_token=entry.id_token,
        refresh_token=entry.refresh_token,
    )
