# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Firebase authentication service with Identity Platform multi-tenancy support."""

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth as firebase_auth
from firebase_admin import tenant_mgt

from ..database import get_admin_firestore_client
from ..models import User
from ..repositories import (
    AllowlistRepository,
    UserRepository,
    get_allowlist_repository,
    get_user_repository,
)
from ..settings import get_settings
from ..version_check import check_client_version
from .firebase_init import initialize_firebase_app

logger = logging.getLogger(__name__)
security = HTTPBearer()

TENANT_CACHE_TTL = 300  # 5 minutes


@dataclass(frozen=True)
class TenantContext:
    """Authenticated user context with optional tenant information.

    When multi-tenancy is enabled, tenant_id is extracted from the
    firebase.tenant JWT claim and resolved to a Firestore database name.
    """

    user_id: str
    tenant_id: str | None = None
    firestore_db: str = "(default)"


@dataclass
class _TenantCacheEntry:
    database_name: str
    status: str
    expires_at: float

    @property
    def is_expired(self) -> bool:
        return time.monotonic() > self.expires_at


_tenant_cache: dict[str, _TenantCacheEntry] = {}


def verify_firebase_token(token: str) -> dict[str, Any]:
    """
    Verify a Firebase ID token.

    Args:
        token: The Firebase ID token to verify

    Returns:
        Dictionary containing the decoded token claims

    Raises:
        HTTPException: If token is invalid or verification fails
    """
    initialize_firebase_app()
    try:
        decoded_token: dict[str, Any] = firebase_auth.verify_id_token(token, check_revoked=True)
        return decoded_token
    except firebase_auth.ExpiredIdTokenError as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "TOKEN_EXPIRED",
                    "message": "Authentication token has expired",
                    "details": {},
                }
            },
        ) from err
    except firebase_auth.RevokedIdTokenError as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "TOKEN_REVOKED",
                    "message": "Authentication token has been revoked",
                    "details": {},
                }
            },
        ) from err
    except firebase_auth.UserDisabledError as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "USER_DISABLED",
                    "message": "User account has been disabled",
                    "details": {},
                }
            },
        ) from err
    except firebase_auth.InvalidIdTokenError as err:
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


def extract_tenant_id(decoded_token: dict[str, Any]) -> str | None:
    """Extract the tenant ID from a decoded Firebase/Identity Platform token.

    Identity Platform tokens include a `firebase.tenant` claim when the user
    authenticated against a specific tenant. Returns None for non-tenant tokens
    (single-tenant mode or admin users).
    """
    tenant: str | None = decoded_token.get("firebase", {}).get("tenant")
    return tenant


def resolve_tenant_database(tenant_id: str, admin_db: Any) -> str | None:
    """Resolve a tenant ID to its Firestore database name.

    Uses an in-memory cache with TTL to avoid hitting the admin DB on every request.
    Returns None if the tenant doesn't exist or is disabled.
    """
    entry = _tenant_cache.get(tenant_id)
    if entry and not entry.is_expired:
        if entry.status != "active":
            return None
        return entry.database_name

    doc = admin_db.collection("tenants").document(tenant_id).get()
    if not doc.exists:
        return None

    data = doc.to_dict()
    db_name: str = data["firestore_database"]
    tenant_status: str = data.get("status", "active")

    _tenant_cache[tenant_id] = _TenantCacheEntry(
        database_name=db_name,
        status=tenant_status,
        expires_at=time.monotonic() + TENANT_CACHE_TTL,
    )

    if tenant_status != "active":
        return None
    return db_name


def clear_tenant_cache() -> None:
    """Clear the tenant→DB cache. Useful for testing."""
    _tenant_cache.clear()


def get_current_user_id(
    auth_credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    """
    Extract and validate user ID from Firebase ID token.

    This is a FastAPI dependency that can be injected into route handlers.

    Args:
        auth_credentials: HTTP Bearer token credentials from the Authorization header

    Returns:
        The authenticated user's ID (uid from Firebase token)

    Raises:
        HTTPException: If authentication fails
    """
    token = auth_credentials.credentials
    decoded_token = verify_firebase_token(token)
    user_id = decoded_token.get("uid")

    if not user_id:
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

    return str(user_id)


def require_mfa(
    auth_credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict[str, Any]:
    """
    Verify that the user authenticated with MFA.

    Checks for the `firebase.sign_in_second_factor` claim in the token.
    Skipped when `settings.require_mfa` is False or in development mode.

    Returns:
        Decoded token claims

    Raises:
        HTTPException: 403 if MFA not used when required
    """
    token = auth_credentials.credentials
    decoded_token = verify_firebase_token(token)

    settings = get_settings()
    if not settings.require_mfa:
        return decoded_token
    if settings.is_development:
        logger.debug("MFA check skipped (development mode)")
        return decoded_token
    if settings.auth_mode == "iap":
        logger.debug("MFA check skipped (IAP mode — access control at load balancer)")
        return decoded_token

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

    return decoded_token


def get_tenant_context(
    decoded_token: dict[str, Any] = Depends(require_mfa),
    user_repo: UserRepository = Depends(get_user_repository),
) -> TenantContext:
    """FastAPI dependency: resolve authenticated user to a TenantContext.

    In single-tenant mode (multi_tenancy_enabled=False), returns a default context.
    In multi-tenant mode, extracts the tenant from the JWT and resolves to a DB name.
    Platform admins (no tenant claim + is_admin) get admin-only access.
    """
    user_id = decoded_token.get("uid")
    if not user_id:
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

    settings = get_settings()
    if not settings.multi_tenancy_enabled:
        return TenantContext(user_id=str(user_id))

    tenant_id = extract_tenant_id(decoded_token)
    if not tenant_id:
        # No tenant claim — check if platform admin
        user = user_repo.get(str(user_id))
        if user and user.is_admin:
            return TenantContext(
                user_id=str(user_id),
                firestore_db=settings.admin_database,
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "MISSING_TENANT",
                    "message": "Tenant claim required",
                    "details": {},
                }
            },
        )

    admin_db = get_admin_firestore_client()
    db_name = resolve_tenant_database(tenant_id, admin_db)
    if not db_name:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "UNKNOWN_TENANT",
                    "message": "Tenant not found",
                    "details": {},
                }
            },
        )

    return TenantContext(
        user_id=str(user_id),
        tenant_id=tenant_id,
        firestore_db=db_name,
    )


def get_current_user_no_mfa(
    request: Request,
    auth_credentials: HTTPAuthorizationCredentials = Depends(security),
    user_repo: UserRepository = Depends(get_user_repository),
    allowlist_repo: AllowlistRepository = Depends(get_allowlist_repository),
) -> User:
    """
    Get current user with token verification but WITHOUT MFA enforcement.

    Used by pre-MFA-enrollment endpoints (e.g., /api/users/me/status)
    so the dashboard layout can check if MFA enrollment is needed.
    """
    decoded_token = verify_firebase_token(auth_credentials.credentials)
    tenant_id_header = request.headers.get("X-Tenant-ID")
    return _resolve_user(decoded_token, user_repo, allowlist_repo, tenant_id_header)


def _resolve_user(
    decoded_token: dict[str, Any],
    user_repo: UserRepository,
    allowlist_repo: AllowlistRepository,
    tenant_id_header: str | None = None,
) -> User:
    """
    Resolve a user from a decoded token, auto-provisioning on first login.

    Shared logic for get_current_user and get_current_user_no_mfa.
    """
    user_id = decoded_token.get("uid")

    if not user_id:
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

    user = user_repo.get(str(user_id))
    if not user:
        email = decoded_token.get("email", "")

        # Fallback: token may lack email claim after refresh by next-firebase-auth-edge.
        # Try firebase.identities first, then look up from Firebase Auth directly.
        if not email:
            firebase_claims = decoded_token.get("firebase", {})
            identities = firebase_claims.get("identities", {})
            email_list = identities.get("email", [])
            if email_list:
                email = email_list[0]

        if not email:
            try:
                token_tenant = decoded_token.get("firebase", {}).get("tenant")
                if token_tenant and tenant_id_header and token_tenant != tenant_id_header:
                    logger.warning(
                        "Tenant mismatch rejected: JWT tenant=%s, header tenant=%s, uid=%s",
                        token_tenant, tenant_id_header, user_id,
                    )
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail={
                            "error": {
                                "code": "TENANT_MISMATCH",
                                "message": "Token tenant does not match request tenant",
                                "details": {},
                            }
                        },
                    )
                tenant_id = token_tenant or tenant_id_header
                logger.info(
                    "Email lookup: uid=%s, tenant_source=%s",
                    user_id,
                    "jwt" if token_tenant else ("header" if tenant_id_header else "none"),
                )
                if tenant_id:
                    tenant_auth = tenant_mgt.auth_for_tenant(tenant_id)
                    fb_user = tenant_auth.get_user(str(user_id))
                else:
                    fb_user = firebase_auth.get_user(str(user_id))
                email = fb_user.email or ""
                logger.info("Resolved email from Firebase Auth: uid=%s", user_id)
            except Exception as exc:
                logger.warning(
                    "Could not look up email from Firebase Auth for uid=%s: %s",
                    user_id, exc,
                )

        # Defense-in-depth: check allowlist before auto-provisioning
        settings = get_settings()
        if settings.restrict_signups and (not email or not allowlist_repo.is_allowed(email)):
            logger.warning("Blocked non-allowlisted user: uid=%s", user_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": {
                        "code": "SIGNUP_NOT_ALLOWED",
                        "message": "Your email is not authorized to access this platform",
                        "details": {},
                    }
                },
            )

        # Auto-provision user on first login from Firebase token claims
        user = User(
            id=str(user_id),
            email=email,
            name=decoded_token.get("name", decoded_token.get("email", "User")),
            created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            picture=decoded_token.get("picture"),
            status="approved",
        )
        user_repo.update(user)
        logger.info("Auto-provisioned user %s", user.id)

    # Check user status
    if user.status == "disabled":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "USER_DISABLED",
                    "message": "Your account has been disabled",
                    "details": {},
                }
            },
        )

    return user


def get_current_user(
    request: Request,
    decoded_token: dict[str, Any] = Depends(require_mfa),
    user_repo: UserRepository = Depends(get_user_repository),
    allowlist_repo: AllowlistRepository = Depends(get_allowlist_repository),
) -> User:
    """
    Get the current authenticated user, auto-provisioning on first login.

    Depends on require_mfa to avoid double token verification.
    Checks client version, allowlist before provisioning, and user status after lookup.

    Raises:
        HTTPException: If authentication fails, client is outdated, or user is not allowed
    """
    check_client_version(request)
    tenant_id_header = request.headers.get("X-Tenant-ID")
    return _resolve_user(decoded_token, user_repo, allowlist_repo, tenant_id_header)


def require_baa_acceptance(
    user: User = Depends(get_current_user),
) -> User:
    """
    Verify that the user has accepted the Business Associate Agreement.

    HIPAA REQUIREMENT: Users must accept BAA before accessing Protected Health
    Information (PHI). This dependency should be used on all routes that access
    patient data or other PHI.

    For self-hosted installations, this check can be disabled by setting
    REQUIRE_BAA=false in the environment.

    Args:
        user: Current authenticated user

    Returns:
        User object if BAA is accepted

    Raises:
        HTTPException: 403 Forbidden if BAA not accepted (when REQUIRE_BAA=true)
    """
    settings = get_settings()
    if not settings.require_baa:
        return user

    if not user.baa_accepted_at:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "BAA_NOT_ACCEPTED",
                    "message": (
                        "Business Associate Agreement must be accepted "
                        "before accessing patient data"
                    ),
                    "details": {
                        "baa_accepted": False,
                        "message": (
                            "Please review and accept the Business Associate Agreement "
                            "to continue"
                        ),
                    },
                }
            },
        )
    return user


def require_admin(
    user: User = Depends(get_current_user),
) -> User:
    """
    Verify user is admin. Bypasses in development mode.

    Args:
        user: Current authenticated user

    Returns:
        User object if admin or in dev mode

    Raises:
        HTTPException: 403 if not admin in production
    """
    settings = get_settings()

    # Bypass in dev mode (startup warning emitted in main.py)
    if settings.is_development:
        logger.debug("Admin check skipped for user %s (development mode)", user.id)
        return user

    # Enforce in production
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "ADMIN_REQUIRED",
                    "message": "Admin privileges required",
                    "details": {},
                }
            },
        )
    return user


def get_baa_version() -> str:
    """
    Get the current BAA version identifier.

    Dynamically determines the latest version by finding the most recent
    BAA-{version}.md file in the baa/ directory.
    """
    baa_dir = Path(__file__).parent.parent.parent / "baa"
    baa_files = sorted(baa_dir.glob("BAA-*.md"), reverse=True)

    if not baa_files:
        raise RuntimeError("No BAA files found in baa/ directory")

    # Extract version from filename: BAA-2024-01-01.md -> 2024-01-01
    latest_file = baa_files[0]
    version = latest_file.stem.replace("BAA-", "")
    return version
