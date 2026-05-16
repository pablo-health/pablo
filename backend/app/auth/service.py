# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Firebase authentication service with practice-based access control."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth as firebase_auth

from ..logging_config import tenant_id_var, user_id_var
from ..models import User
from ..repositories import (
    AllowlistRepository,
    IdentityRepository,
    UserRepository,
    get_allowlist_repository,
    get_identity_repository,
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

    In multi-tenant mode the user's email is resolved to a practice
    via the platform.email_tenant_mappings table. The practice_schema
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


def _resolve_pablo_user_id(
    request: Request | None,
    firebase_uid: str,
    identity_repo: IdentityRepository,
    *,
    create_if_missing: bool,
) -> str:
    """Translate a Firebase uid to Pablo's internal user_id.

    Pablo decouples its storage identity from the auth provider's
    subject ID via the ``platform.user_identities`` mapping table.
    Routes use the value returned here for every downstream DB
    operation, so migrating off a provider (or linking a second
    provider to the same user) is a row insert, not a schema rewrite.

    ``create_if_missing=False`` is the lookup-only path used by general
    request dependencies. If no mapping exists yet — e.g., for a user
    provisioned before the indirection table — it falls back to the
    Firebase uid. The auto-provision path (`_resolve_user`) passes
    ``create_if_missing=True`` so the first successful auth pass
    establishes the mapping.

    Result is cached on ``request.state`` to avoid re-resolving across
    multiple dependencies in the same request.
    """
    # Only the "real mapping found" path is cacheable. A fallback to
    # firebase_uid (no mapping yet) must not poison the cache, or a
    # later auto-provision call would short-circuit and skip the
    # resolve_or_create.
    if request is not None and not create_if_missing:
        cached_uid = getattr(request.state, "pablo_user_id_firebase_uid", None)
        cached_pid = getattr(request.state, "pablo_user_id", None)
        if (
            isinstance(cached_uid, str)
            and cached_uid == firebase_uid
            and isinstance(cached_pid, str)
        ):
            return cached_pid

    if create_if_missing:
        pablo_id = identity_repo.resolve_or_create("firebase", firebase_uid)
        cacheable = True
    else:
        looked_up = identity_repo.get_user_id("firebase", firebase_uid)
        cacheable = looked_up is not None
        pablo_id = looked_up or firebase_uid

    if request is not None and cacheable:
        request.state.pablo_user_id = pablo_id
        request.state.pablo_user_id_firebase_uid = firebase_uid
    return pablo_id


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
    identity_repo: IdentityRepository = Depends(get_identity_repository),
) -> str:
    """
    Extract and validate user ID from Firebase ID token.

    Returns Pablo's internal user_id (resolved via the user_identities
    mapping), not the raw Firebase uid. For users provisioned before the
    mapping existed, falls back to the Firebase uid so legacy rows
    continue to match — `_resolve_user` is what bootstraps the mapping
    on the auto-provision path.

    Args:
        request: The current HTTP request (for middleware token cache)
        auth_credentials: HTTP Bearer token credentials from the Authorization header
        identity_repo: Identity-mapping repository

    Returns:
        The Pablo-internal user ID for the authenticated user.

    Raises:
        HTTPException: If authentication fails
    """
    token = auth_credentials.credentials
    decoded_token = _get_cached_token(request, token)
    if decoded_token is None:
        decoded_token = verify_firebase_token(token)
    firebase_uid = decoded_token.get("uid")

    if not firebase_uid:
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

    pablo_user_id = _resolve_pablo_user_id(
        request, str(firebase_uid), identity_repo, create_if_missing=False
    )
    user_id_var.set(str(pablo_user_id))
    return pablo_user_id


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


def enforce_idle_session(
    decoded_token: dict[str, Any] = Depends(require_mfa),
) -> dict[str, Any]:
    """Reject requests whose Firebase session has been idle past the timeout.

    Drop-in replacement for ``require_mfa`` in dependency chains: returns
    the same decoded token so downstream deps don't need to change.
    Defers the Redis logic to ``idle_session.check_and_touch`` to keep
    that module free of imports from this file.
    """
    from . import idle_session

    idle_session.check_and_touch(decoded_token)
    return decoded_token


def get_tenant_context(
    request: Request,
    decoded_token: dict[str, Any] = Depends(enforce_idle_session),
    user_repo: UserRepository = Depends(get_user_repository),
    identity_repo: IdentityRepository = Depends(get_identity_repository),
) -> TenantContext:
    """FastAPI dependency: resolve authenticated user to a TenantContext.

    In single-tenant mode, returns a default context.
    In multi-tenant mode, resolves the user's email to a practice via
    Postgres. Platform admins without a practice mapping get admin-only
    access.
    """
    firebase_uid = decoded_token.get("uid")
    if not firebase_uid:
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

    pablo_user_id = _resolve_pablo_user_id(
        request, str(firebase_uid), identity_repo, create_if_missing=False
    )
    user_id_var.set(str(pablo_user_id))

    settings = get_settings()
    if not settings.multi_tenancy_enabled:
        return TenantContext(user_id=pablo_user_id)

    # Resolve practice from user's email
    email = _extract_email(decoded_token)
    if email:
        practice = _resolve_practice_from_email(email)
        if practice:
            practice_id, schema_name = practice
            tenant_id_var.set(practice_id)
            # search_path is already set by DatabaseSessionMiddleware
            # before any dependency runs — see
            # `app.db.middleware.DatabaseSessionMiddleware._resolve_schema_from_request`.
            # We still need the active session here to set the
            # RLS user-id variable below.
            from ..db import get_db_session

            session = get_db_session()

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
                {"uid": pablo_user_id},
            )
            return TenantContext(
                user_id=pablo_user_id,
                practice_id=practice_id,
                practice_schema=schema_name,
            )

    # No practice mapping — check if platform admin
    user = user_repo.get(pablo_user_id)
    if user and user.is_admin:
        return TenantContext(user_id=pablo_user_id)

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
    identity_repo: IdentityRepository = Depends(get_identity_repository),
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
    return _resolve_user(decoded_token, user_repo, allowlist_repo, identity_repo, request)


def _resolve_user(
    decoded_token: dict[str, Any],
    user_repo: UserRepository,
    allowlist_repo: AllowlistRepository,
    identity_repo: IdentityRepository,
    request: Request | None = None,
) -> User:
    """Resolve a user from a decoded token, auto-provisioning on first login.

    Looks up Pablo's internal user_id via the user_identities mapping
    (lazy-creating on first sign-in). The new user record is keyed by
    that internal id, not the Firebase uid — that's the decoupling that
    makes a future provider migration a row insert rather than a
    full-schema rewrite.
    """
    firebase_uid = decoded_token.get("uid")

    if not firebase_uid:
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

    firebase_uid_str = str(firebase_uid)

    # Lookup-only first: if a mapping exists, use it. If not, defer the
    # creation until after the allowlist gate so rejected users don't
    # leave stray rows.
    pablo_user_id = _resolve_pablo_user_id(
        request, firebase_uid_str, identity_repo, create_if_missing=False
    )

    user = user_repo.get(pablo_user_id)
    if not user:
        email = _extract_email(decoded_token)

        # Fallback: look up email from Firebase Auth if token lacks it
        if not email:
            try:
                fb_user = firebase_auth.get_user(firebase_uid_str)
                email = (fb_user.email or "").lower()
                logger.info("Resolved email from Firebase Auth: uid=%s", firebase_uid_str)
            except Exception as exc:
                logger.warning(
                    "Could not look up email from Firebase Auth for uid=%s: %s",
                    firebase_uid_str,
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
            logger.warning("Blocked non-allowlisted user: uid=%s", firebase_uid_str)
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

        # Allowlist passed — link the identity (or no-op if already linked
        # via the legacy backfill) so downstream queries see a stable
        # Pablo user_id.
        pablo_user_id = _resolve_pablo_user_id(
            request, firebase_uid_str, identity_repo, create_if_missing=True
        )

        # Auto-provision user on first login from Firebase token claims
        user = User(
            id=pablo_user_id,
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

    user_id_var.set(str(pablo_user_id))
    return user


def get_current_user(
    request: Request,
    decoded_token: dict[str, Any] = Depends(enforce_idle_session),
    user_repo: UserRepository = Depends(get_user_repository),
    allowlist_repo: AllowlistRepository = Depends(get_allowlist_repository),
    identity_repo: IdentityRepository = Depends(get_identity_repository),
) -> User:
    """Get the current authenticated user, auto-provisioning on first login.

    Depends on require_mfa to avoid double token verification.
    Checks client version, allowlist before provisioning, and user status after lookup.
    """
    check_client_version(request)
    return _resolve_user(decoded_token, user_repo, allowlist_repo, identity_repo, request)


def require_active_subscription(
    user: User = Depends(get_current_user),
) -> User:
    """Verify the user's practice has an active (or trial/grace) subscription.

    No-op when subscription enforcement is disabled (the single-tenant
    default).

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


def _verify_google_oidc_token(token: str, audience: str) -> dict[str, object]:
    """Verify a Google-signed OIDC ID token, returning its claims. Raises ValueError on failure."""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token

    claims: dict[str, object] = google_id_token.verify_oauth2_token(
        token, google_requests.Request(), audience=audience
    )
    issuer = claims.get("iss")
    if issuer not in ("https://accounts.google.com", "accounts.google.com"):
        msg = f"unexpected issuer {issuer!r}"
        raise ValueError(msg)
    if not claims.get("email_verified"):
        msg = "email_verified claim missing or false"
        raise ValueError(msg)
    return claims


def require_pentest_runner(request: Request) -> str:
    """Gate pentest-admin endpoints to the pentest-runner service account."""
    settings = get_settings()
    if not settings.pentest_runner_sa_email or not settings.pentest_runner_audience:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": {
                    "code": "PENTEST_RUNNER_NOT_CONFIGURED",
                    "message": "Pentest runner identity is not configured.",
                    "details": {},
                }
            },
        )

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "PENTEST_RUNNER_REQUIRED",
                    "message": "Missing bearer token.",
                    "details": {},
                }
            },
        )
    token = auth_header[7:]

    try:
        claims = _verify_google_oidc_token(token, audience=settings.pentest_runner_audience)
    except Exception as exc:
        logger.warning("Pentest runner OIDC verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "PENTEST_RUNNER_REQUIRED",
                    "message": "Invalid pentest runner token.",
                    "details": {},
                }
            },
        ) from exc

    email = str(claims.get("email", "")).lower()
    if email != settings.pentest_runner_sa_email.lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "PENTEST_RUNNER_REQUIRED",
                    "message": "Token does not belong to the pentest runner.",
                    "details": {},
                }
            },
        )
    return email


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

    Deployments that bundle ``baa/BAA-YYYY-MM-DD.md`` require in-app
    acceptance. Deployments without any bundled BAA files disable the
    in-app flow — operators sign their BAA directly with their cloud
    provider.
    """
    baa_dir = Path(__file__).parent.parent.parent / "baa"
    if not baa_dir.is_dir():
        return ""
    baa_files = sorted(baa_dir.glob("BAA-*.md"), reverse=True)
    if not baa_files:
        return ""
    return baa_files[0].stem.removeprefix("BAA-")
