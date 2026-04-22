# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Firebase authentication service with practice-based access control."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth as firebase_auth

from ..models import User
from ..repositories import (
    AllowlistRepository,
    UserRepository,
    get_allowlist_repository,
    get_user_repository,
)
from ..settings import get_settings
from ..utcnow import utc_now
from ..version_check import check_client_version
from .firebase_init import initialize_firebase_app

logger = logging.getLogger(__name__)
security = HTTPBearer()


@dataclass(frozen=True)
class TenantContext:
    """Authenticated user context with practice information.

    For SaaS mode, the user's email is resolved to a practice via
    the platform.email_tenant_mappings table. The practice_schema
    determines which Postgres schema to query.
    """

    user_id: str
    practice_id: str | None = None
    practice_schema: str | None = None


def _get_cached_token(request: Request | None, token: str) -> dict[str, Any] | None:
    """Return middleware-cached decoded token if it matches the current JWT.

    The DatabaseSessionMiddleware verifies the Firebase token during schema
    resolution and caches the result on request.state. This avoids a second
    round-trip to Firebase (revocation check + crypto) in the dependency chain.
    """
    if request is None:
        return None
    cached_raw = getattr(request.state, "verified_firebase_token_raw", None)
    if cached_raw is not None and cached_raw == token:
        return request.state.decoded_firebase_token  # type: ignore[no-any-return]
    return None


def verify_firebase_token(token: str) -> dict[str, Any]:
    """Verify a Firebase ID token (project-level, single-pass).

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


def get_current_user_id(
    request: Request,
    auth_credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    """
    Extract and validate user ID from Firebase ID token.

    This is a FastAPI dependency that can be injected into route handlers.

    Args:
        request: The current HTTP request (for middleware token cache)
        auth_credentials: HTTP Bearer token credentials from the Authorization header

    Returns:
        The authenticated user's ID (uid from Firebase token)

    Raises:
        HTTPException: If authentication fails
    """
    token = auth_credentials.credentials
    decoded_token = _get_cached_token(request, token)
    if decoded_token is None:
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
    request: Request,
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
    decoded_token = _get_cached_token(request, token)
    if decoded_token is None:
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

    # E2E test accounts bypass MFA in non-production environments only
    is_prod_project = settings.gcp_project_id.endswith("-prod")
    if settings.e2e_test_emails and not is_prod_project:
        email = decoded_token.get("email", "")
        if email in settings.e2e_test_emails and decoded_token.get("email_verified", False):
            logger.warning("MFA bypassed for E2E test account: uid=%s", decoded_token.get("uid"))
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

    In single-tenant mode, returns a default context.
    In SaaS mode, resolves the user's email to a practice via Postgres.
    Platform admins without a practice mapping get admin-only access.
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

    # Resolve practice from user's email
    email = _extract_email(decoded_token)
    if email:
        practice = _resolve_practice_from_email(email)
        if practice:
            practice_id, schema_name = practice
            # CRITICAL: Switch the DB session to this tenant's schema.
            # Without this, all queries hit the default 'practice' schema,
            # violating tenant isolation (HIPAA).
            from ..db import get_db_session, set_tenant_schema

            session = get_db_session()
            set_tenant_schema(session, schema_name)

            # RLS defense-in-depth: set the current user ID as a
            # transaction-scoped session variable so PostgreSQL
            # row-level security policies can enforce per-clinician
            # isolation within the tenant schema.
            # Uses set_config() instead of SET LOCAL because SET
            # doesn't support bind parameters. The third arg (true)
            # makes it transaction-local — auto-cleared on commit.
            from sqlalchemy import text

            session.execute(
                text("SELECT set_config('app.current_user_id', :uid, true)"),
                {"uid": str(user_id)},
            )
            return TenantContext(
                user_id=str(user_id),
                practice_id=practice_id,
                practice_schema=schema_name,
            )

    # No practice mapping — check if platform admin
    user = user_repo.get(str(user_id))
    if user and user.is_admin:
        return TenantContext(user_id=str(user_id))

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error": {
                "code": "NO_PRACTICE",
                "message": "No practice associated with this account",
                "details": {},
            }
        },
    )


def _extract_email(decoded_token: dict[str, Any]) -> str:
    """Extract email from a decoded Firebase token, with fallbacks."""
    email = decoded_token.get("email", "")
    if not email:
        firebase_claims = decoded_token.get("firebase", {})
        identities = firebase_claims.get("identities", {})
        email_list = identities.get("email", [])
        if email_list:
            email = email_list[0]
    return email.lower() if email else ""


def _resolve_practice_from_email(email: str) -> tuple[str, str] | None:
    """Look up practice_id and schema_name from the platform schema.

    Returns (practice_id, schema_name) or None if not found.
    """
    from ..db import create_standalone_session
    from ..db.platform_models import EmailTenantMappingRow, PracticeRow

    with create_standalone_session() as db:
        mapping = db.get(EmailTenantMappingRow, email)
        if not mapping:
            return None
        practice = db.get(PracticeRow, mapping.practice_id)
        if not practice or not practice.is_active:
            return None
        return (practice.id, practice.schema_name)


def get_current_user_no_mfa(
    request: Request,
    auth_credentials: HTTPAuthorizationCredentials = Depends(security),
    user_repo: UserRepository = Depends(get_user_repository),
    allowlist_repo: AllowlistRepository = Depends(get_allowlist_repository),
) -> User:
    """Get current user with token verification but WITHOUT MFA enforcement.

    Used by pre-MFA-enrollment endpoints (e.g., /api/users/me/status)
    so the dashboard layout can check if MFA enrollment is needed.

    No tenant schema resolution needed — user_repo reads from platform.users.
    """
    token = auth_credentials.credentials
    decoded_token = _get_cached_token(request, token)
    if decoded_token is None:
        decoded_token = verify_firebase_token(token)
    return _resolve_user(decoded_token, user_repo, allowlist_repo)


def _resolve_user(
    decoded_token: dict[str, Any],
    user_repo: UserRepository,
    allowlist_repo: AllowlistRepository,
) -> User:
    """Resolve a user from a decoded token, auto-provisioning on first login."""
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
        email = _extract_email(decoded_token)

        # Fallback: look up email from Firebase Auth if token lacks it
        if not email:
            try:
                fb_user = firebase_auth.get_user(str(user_id))
                email = (fb_user.email or "").lower()
                logger.info("Resolved email from Firebase Auth: uid=%s", user_id)
            except Exception as exc:
                logger.warning(
                    "Could not look up email from Firebase Auth for uid=%s: %s",
                    user_id,
                    exc,
                )

        # Defense-in-depth: check allowlist before auto-provisioning.
        # The ephemeral pentest users (pentestuser-<8hex>@pablo.health)
        # are test-only identities created on every pentest run — they
        # get a dedicated bypass so the pentest Cloud Run Job doesn't
        # need write access to `platform.allowed_emails` (the
        # read-only-DB rule for pentests). The prefix is reserved: real
        # signups matching this pattern are rejected upstream.
        from ..jobs.pentest_identity import PENTEST_EMAIL_PATTERN

        settings = get_settings()
        is_pentest_user = bool(email and PENTEST_EMAIL_PATTERN.match(email))
        if (
            settings.restrict_signups
            and not is_pentest_user
            and (not email or not allowlist_repo.is_allowed(email))
        ):
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
            created_at=utc_now(),
            picture=decoded_token.get("picture"),
            status="approved",
        )
        user_repo.update(user)
        logger.info("Auto-provisioned user %s", user.id)

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
    """Get the current authenticated user, auto-provisioning on first login.

    Depends on require_mfa to avoid double token verification.
    Checks client version, allowlist before provisioning, and user status after lookup.
    """
    check_client_version(request)
    return _resolve_user(decoded_token, user_repo, allowlist_repo)


def require_active_subscription(
    user: User = Depends(get_current_user),
) -> User:
    """Verify the user's practice has an active (or trial/grace) subscription.

    No-op for self-hosted (non-SaaS) installations.

    Raises:
        HTTPException: 403 if subscription is lapsed and no grace extension is active.
    """
    settings = get_settings()
    if not settings.is_saas:
        return user

    from ..routes.subscription import _fetch_subscription  # type: ignore[import-not-found]

    sub = _fetch_subscription(user.email, settings)
    if not sub:
        # No subscription record — might be mid-provisioning; let through
        return user

    effective = sub.get("effective_status", sub.get("status"))
    if effective in ("active", "trial"):
        return user

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error": {
                "code": "SUBSCRIPTION_INACTIVE",
                "message": "Your subscription is not active",
                "details": {
                    "status": sub.get("status"),
                    "grace_extension_available": sub.get("grace_extension_available", False),
                },
            }
        },
    )


def require_baa_acceptance(
    user: User = Depends(require_active_subscription),
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
                            "Please review and accept the Business Associate Agreement to continue"
                        ),
                    },
                }
            },
        )
    return user


PENTEST_RUNNER_EMAIL = "pablo-pentest-runner@pablo.health"


def require_pentest_runner(
    user: User = Depends(get_current_user),
) -> User:
    """Gate pentest-admin endpoints to the dedicated runner identity.

    Deliberately narrower than ``require_admin``: a generic platform
    admin should not be able to provision pentest tenants or toggle
    pentest-tenant lifecycle. Only the pre-provisioned Firebase user
    ``pablo-pentest-runner@pablo.health`` passes this check.

    No dev-mode bypass — tests should use
    ``app.dependency_overrides[require_pentest_runner]`` instead of a
    relaxed production check.

    TODO(security): migrate to Google service-account OIDC tokens +
    Cloud Run ``ingress=internal-and-cloud-load-balancing``. Firebase
    email auth gives "only this identity can call," not "only this
    machine can call" — a stolen password/TOTP works from anywhere.
    OIDC + internal ingress cryptographically binds the call to the
    pentest runner service account AND blocks public-internet reach.
    Track in the audit-hardening epic.
    """
    if user.email.lower() != PENTEST_RUNNER_EMAIL:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "PENTEST_RUNNER_REQUIRED",
                    "message": "Pentest operations require the pentest runner identity.",
                    "details": {},
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
    """Return the latest BAA version, or "" if no BAA files are bundled.

    SaaS builds ship baa/BAA-YYYY-MM-DD.md and require acceptance.
    Core (OSS) self-hosters sign their BAA directly with their cloud
    provider — no BAA files are bundled and the in-app flow is disabled.
    """
    baa_dir = Path(__file__).parent.parent.parent / "baa"
    if not baa_dir.is_dir():
        return ""
    baa_files = sorted(baa_dir.glob("BAA-*.md"), reverse=True)
    if not baa_files:
        return ""
    return baa_files[0].stem.removeprefix("BAA-")
