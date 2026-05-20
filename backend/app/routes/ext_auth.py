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
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr

from ..auth.route_security import truly_public
from ..auth.service import E2E_EMAIL_PATTERN
from ..db import get_db_session
from ..db.platform_models import EmailTenantMappingRow
from ..jobs.pentest_identity import PENTEST_EMAIL_PATTERN
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
    layers of defense — all required, fail-closed:
      1. Signature — token is signed by Google.
      2. Audience — token targets this backend (settings.backend_base_url).
      3. Issuer — iss is an accepted Google identity issuer.
      4. Caller — token's email claim matches the configured blocking
         function service account.

    If audience / caller SA are unconfigured, the endpoint returns 503 rather
    than skipping the check. Any Google-signed OIDC token would otherwise pass
    the signature + issuer + email_verified gates, which would allow any
    Google principal to invoke the blocking endpoint.
    """
    settings = get_settings()
    if settings.is_development:
        return

    expected_audience = settings.backend_base_url or None
    expected_caller = settings.blocking_function_service_account or None

    if expected_audience is None or expected_caller is None:
        logger.error(
            "Blocking function OIDC checks misconfigured: "
            "backend_base_url=%s blocking_function_service_account=%s",
            "set" if expected_audience else "UNSET",
            "set" if expected_caller else "UNSET",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Blocking function auth is not configured for this deployment",
        )

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Service auth required",
        )

    token = auth_header.removeprefix("Bearer ")

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
        logger.warning("Rejected blocking-function caller: bad issuer=%s", claims.get("iss"))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid service identity token",
        )

    if not claims.get("email_verified"):
        logger.warning("Rejected blocking-function caller: email not verified")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid service identity token",
        )

    if claims.get("email") != expected_caller:
        logger.warning(
            "Rejected blocking-function caller: caller=%s expected=%s",
            claims.get("email"),
            expected_caller,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid service identity token",
        )


@router.post("/check-allowlist", response_model=CheckAllowlistResponse)
def check_allowlist(
    request: CheckAllowlistRequest,
    http_request: Request,
    _public: None = Depends(truly_public),
) -> CheckAllowlistResponse:
    """Check if an email is on the allowlist.

    Called by the beforeCreate blocking function to gate account creation.
    """
    _verify_blocking_function_token(http_request)
    settings = get_settings()

    # If signups aren't restricted, everyone is allowed
    if not settings.restrict_signups:
        return CheckAllowlistResponse(allowed=True)

    # Reserved test-identity prefixes bypass the allowlist (same as
    # auth/service.py — see comment there). Without this duplicate
    # check, the beforeCreate blocking function rejects test users
    # before they're even created in Firebase, so the bypass in
    # auth/service.py (which runs on API calls AFTER signUp) never
    # gets a chance to apply.
    #
    # Production guard: Firebase Email/Password signup does not require
    # email-link confirmation, so an attacker who can hit Firebase
    # signUp against the prod project (the web API key ships in the
    # frontend bundle) could mint a token for any e2etest-<hex> /
    # pentestuser-<hex>@pablo.health address and ride this bypass. In
    # -prod, force the patterns through the normal allowlist gate.
    is_prod_project = settings.gcp_project_id.endswith("-prod")
    email_lower = request.email.lower()
    if not is_prod_project and (
        PENTEST_EMAIL_PATTERN.match(email_lower) or E2E_EMAIL_PATTERN.match(email_lower)
    ):
        return CheckAllowlistResponse(allowed=True)

    repo = get_allowlist_repository()
    if repo.is_allowed(request.email):
        return CheckAllowlistResponse(allowed=True)

    # A provisioned tenant is an implicit allowlist entry: if the marketing
    # signup flow already created an EmailTenantMappingRow for this email,
    # let Firebase create the account. Without this, self-serve signup
    # cannot complete when restrict_signups is on, because provisioning
    # populates the tenant mapping but not platform.allowed_emails.
    if settings.multi_tenancy_enabled:
        session = get_db_session()
        mapping = session.get(EmailTenantMappingRow, request.email.lower())
        if mapping is not None:
            return CheckAllowlistResponse(allowed=True)

    return CheckAllowlistResponse(allowed=False)


@router.post("/check-status", response_model=CheckStatusResponse)
def check_status(
    request: CheckStatusRequest,
    http_request: Request,
    _public: None = Depends(truly_public),
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
