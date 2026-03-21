# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Pre-auth endpoints for tenant resolution, signup, and native app code exchange.

These endpoints do NOT require authentication — they run before the user has a JWT.
Security: constant-time responses prevent email enumeration.
"""

import asyncio
import logging
import time
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from firebase_admin import auth as firebase_auth
from pydantic import BaseModel, EmailStr

from ..auth.firebase_init import initialize_firebase_app
from ..database import get_admin_firestore_client
from ..rate_limit import require_rate_limit
from ..services.auth_code_store import create_auth_code, exchange_auth_code
from ..settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

RESOLVE_MIN_DURATION = 0.15  # 150ms minimum response time

class ResolveTenantRequest(BaseModel):
    email: EmailStr

class ResolveTenantResponse(BaseModel):
    status: str = "ok"
    tenant_id: str | None = None

class SignupRequest(BaseModel):
    email: EmailStr
    practice_name: str

class SignupResponse(BaseModel):
    status: str = "ok"
    tenant_id: str | None = None

@router.post("/resolve-tenant", response_model=ResolveTenantResponse)
async def resolve_tenant(
    request: ResolveTenantRequest, _: None = Depends(require_rate_limit)
) -> ResolveTenantResponse:
    """Resolve an email to a tenant ID for pre-auth tenant selection.

    Always returns 200 with identical response shape to prevent email enumeration.
    Uses constant-time responses (padded to RESOLVE_MIN_DURATION).
    """
    start = time.monotonic()

    settings = get_settings()
    if not settings.multi_tenancy_enabled:
        # Single-tenant mode — no tenant resolution needed
        await _pad_response_time(start)
        return ResolveTenantResponse()

    admin_db = get_admin_firestore_client()
    doc = admin_db.collection("email_tenants").document(request.email.lower()).get()
    tenant_id = doc.to_dict().get("tenant_id") if doc.exists else None

    await _pad_response_time(start)
    return ResolveTenantResponse(tenant_id=tenant_id)

@router.post("/signup", response_model=SignupResponse)
async def signup(
    request: SignupRequest, _: None = Depends(require_rate_limit)
) -> SignupResponse:
    """Self-service signup: provision a new practice for an allowlisted email.

    Always returns 200 to prevent email enumeration.
    """
    settings = get_settings()
    if not settings.multi_tenancy_enabled:
        return SignupResponse()

    admin_db = get_admin_firestore_client()

    # Check allowlist
    email_lower = request.email.lower()
    allowed_doc = admin_db.collection("allowed_emails").document(email_lower).get()
    if not allowed_doc.exists:
        # Not allowlisted — return generic response (no enumeration)
        return SignupResponse()

    # Check if already provisioned
    existing = admin_db.collection("email_tenants").document(email_lower).get()
    if existing.exists:
        # Already has a tenant — return it
        return SignupResponse(tenant_id=existing.to_dict().get("tenant_id"))

    return SignupResponse()

async def _pad_response_time(start: float) -> None:
    """Pad response to constant minimum duration to prevent timing attacks."""
    elapsed = time.monotonic() - start
    if elapsed < RESOLVE_MIN_DURATION:
        await asyncio.sleep(RESOLVE_MIN_DURATION - elapsed)

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
    # Allow localhost loopback for Windows (RFC 8252 Section 7.3)
    return parsed.scheme == "http" and parsed.hostname == "localhost"

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
    request: CreateAuthCodeRequest, _: None = Depends(require_rate_limit)
) -> CreateAuthCodeResponse:
    """Generate a one-time authorization code for native app auth.

    The frontend calls this after Firebase authentication succeeds,
    passing the tokens. Returns an opaque code (60s TTL, single-use)
    that the native app exchanges for tokens via /native/exchange.

    The id_token is verified server-side before issuing a code to
    prevent storing arbitrary or forged payloads.
    """
    if not _is_valid_native_redirect_uri(request.redirect_uri):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid redirect_uri.",
        )

    # Verify the Firebase id_token before issuing a code
    initialize_firebase_app()
    try:
        decoded_token = firebase_auth.verify_id_token(request.id_token, check_revoked=True)
    except Exception as err:
        logger.warning("Native code request with invalid id_token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired id_token.",
        ) from err

    # Enforce MFA: reject tokens without a completed second factor
    settings = get_settings()
    if (
        settings.require_mfa
        and not settings.is_development
        and settings.auth_mode != "iap"
    ):
        firebase_claims = decoded_token.get("firebase", {})
        if not firebase_claims.get("sign_in_second_factor"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": {
                        "code": "MFA_REQUIRED",
                        "message": "Multi-factor authentication is required",
                        "details": {},
                    }
                },
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired authorization code.",
        )
    # Validate redirect_uri matches what was bound at code creation
    if entry.redirect_uri != request.redirect_uri:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="redirect_uri mismatch.",
        )
    return ExchangeAuthCodeResponse(
        id_token=entry.id_token,
        refresh_token=entry.refresh_token,
    )
