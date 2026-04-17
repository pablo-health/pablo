# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""External auth endpoints — called by Firebase blocking functions.

The blocking functions call these via OIDC-authenticated HTTP so auth
gatekeeping works with the PostgreSQL backend.

Security: OIDC service-to-service auth in production, unauthenticated in dev.
"""

from __future__ import annotations

import logging

import google.auth.transport.requests
import google.oauth2.id_token
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, EmailStr

from ..repositories import get_allowlist_repository, get_user_repository
from ..settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ext/auth", tags=["ext-auth"])


class CheckAllowlistRequest(BaseModel):
    email: EmailStr


class CheckAllowlistResponse(BaseModel):
    allowed: bool


class CheckStatusRequest(BaseModel):
    uid: str


class CheckStatusResponse(BaseModel):
    disabled: bool


_GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")


def _verify_blocking_function_token(request: Request) -> None:
    """Verify OIDC token from Firebase blocking function.

    In development mode, authentication is skipped.

    In production, verifies the Google-signed OIDC identity token with four
    layers of defense:
      1. Signature — token is signed by Google.
      2. Audience — token targets this backend (settings.backend_base_url).
      3. Issuer — iss is an accepted Google identity issuer.
      4. Caller — token's email claim matches the configured blocking
         function service account.

    If audience / caller SA are unconfigured (self-hosted installs that
    haven't set the env vars yet), those checks are skipped and a warning
    is logged. Signature + issuer + email_verified checks always run.
    """
    settings = get_settings()
    if settings.is_development:
        return

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Service auth required",
        )

    token = auth_header.removeprefix("Bearer ")
    expected_audience = settings.backend_base_url or None
    expected_caller = settings.blocking_function_service_account or None

    try:
        request_adapter = google.auth.transport.requests.Request()
        claims = google.oauth2.id_token.verify_token(
            token,
            request_adapter,
            audience=expected_audience,
        )
    except Exception as err:
        logger.warning("Blocking function OIDC verification failed: %s", err)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid service identity token",
        ) from err

    if claims.get("iss") not in _GOOGLE_ISSUERS:
        logger.warning("Rejected blocking function token: issuer=%s", claims.get("iss"))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid service identity token",
        )

    if not claims.get("email_verified"):
        logger.warning("Rejected blocking function token: email not verified")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid service identity token",
        )

    if expected_caller and claims.get("email") != expected_caller:
        logger.warning(
            "Rejected blocking function token: caller=%s expected=%s",
            claims.get("email"),
            expected_caller,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid service identity token",
        )

    if expected_audience is None or expected_caller is None:
        logger.warning(
            "Blocking function OIDC checks are partial: "
            "backend_base_url=%s blocking_function_service_account=%s",
            "set" if expected_audience else "UNSET",
            "set" if expected_caller else "UNSET",
        )


@router.post("/check-allowlist", response_model=CheckAllowlistResponse)
def check_allowlist(
    request: CheckAllowlistRequest,
    http_request: Request,
) -> CheckAllowlistResponse:
    """Check if an email is on the allowlist.

    Called by the beforeCreate blocking function to gate account creation.
    """
    _verify_blocking_function_token(http_request)
    settings = get_settings()

    # If signups aren't restricted, everyone is allowed
    if not settings.restrict_signups:
        return CheckAllowlistResponse(allowed=True)

    repo = get_allowlist_repository()
    return CheckAllowlistResponse(allowed=repo.is_allowed(request.email))


@router.post("/check-status", response_model=CheckStatusResponse)
def check_status(
    request: CheckStatusRequest,
    http_request: Request,
) -> CheckStatusResponse:
    """Check if a user account is disabled.

    Called by the beforeSignIn blocking function to block disabled users.
    """
    _verify_blocking_function_token(http_request)

    repo = get_user_repository()
    user = repo.get(request.uid)
    if user is None:
        # New user — not disabled
        return CheckStatusResponse(disabled=False)

    return CheckStatusResponse(disabled=user.status == "disabled")
