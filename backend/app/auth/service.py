# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Firebase authentication service with practice-based access control."""

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
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
from ..settings import Settings, get_settings
from ..utcnow import utc_now
from ..version_check import check_client_version
from .firebase_init import initialize_firebase_app
from .providers import (
    FirebaseVerifier,
    OidcVerifier,
    VerifiedIdentity,
    VerifierRegistry,
)
from .route_access import (
    AccessIntent,
    AccessLevel,
    access_intent,
    resolve_access_level,
)

logger = logging.getLogger(__name__)

# Reserved E2E test-user prefix. Mirrors PENTEST_EMAIL_PATTERN (see
# jobs/pentest_identity.py). The bypass that honors this pattern is
# gated on settings.is_prod_project in both auth/service.py and
# routes/ext_auth.py.
E2E_EMAIL_PATTERN = re.compile(r"^e2etest-[0-9a-f]{8}@pablo\.health$")
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
    subject_id: str,
    identity_repo: IdentityRepository,
    *,
    create_if_missing: bool,
    provider: str = "firebase",
) -> str:
    """Translate an auth provider's subject id to Pablo's internal user_id.

    Pablo decouples its storage identity from the auth provider's
    subject ID via the ``platform.user_identities`` mapping table, keyed
    on ``(provider, subject_id)``. Routes use the value returned here for
    every downstream DB operation, so migrating off a provider (or
    linking a second provider to the same user) is a row insert, not a
    schema rewrite.

    ``create_if_missing=False`` is the lookup-only path used by general
    request dependencies. If no mapping exists yet — e.g., for a user
    provisioned before the indirection table — it falls back to the
    provider subject id. The auto-provision path (`_resolve_user`) passes
    ``create_if_missing=True`` so the first successful auth pass
    establishes the mapping.

    ``provider`` defaults to ``"firebase"`` so existing call sites are
    unchanged; the dual-issuer path passes the verified identity's
    provider through.

    Result is cached on ``request.state`` to avoid re-resolving across
    multiple dependencies in the same request. The cache key includes the
    provider so two issuers that happen to share a subject id can't
    cross-contaminate.
    """
    # Only the "real mapping found" path is cacheable. A fallback to
    # the subject id (no mapping yet) must not poison the cache, or a
    # later auto-provision call would short-circuit and skip the
    # resolve_or_create.
    if request is not None and not create_if_missing:
        cached_key = getattr(request.state, "pablo_user_id_subject_key", None)
        cached_pid = getattr(request.state, "pablo_user_id", None)
        if (
            isinstance(cached_key, tuple)
            and cached_key == (provider, subject_id)
            and isinstance(cached_pid, str)
        ):
            return cached_pid

    if create_if_missing:
        pablo_id = identity_repo.resolve_or_create(provider, subject_id)
        cacheable = True
    else:
        looked_up = identity_repo.get_user_id(provider, subject_id)
        cacheable = looked_up is not None
        pablo_id = looked_up or subject_id

    if request is not None and cacheable:
        request.state.pablo_user_id = pablo_id
        request.state.pablo_user_id_subject_key = (provider, subject_id)
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
        # Logged so a client that fell behind on token refresh is
        # distinguishable from revoked / malformed tokens when triaging
        # 401s. No PHI: the failure reason only, never token material.
        logger.warning("Firebase ID token rejected: expired (TOKEN_EXPIRED)")
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
        logger.warning("Firebase ID token rejected: revoked (TOKEN_REVOKED)")
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
        logger.warning("Firebase ID token rejected: user disabled (USER_DISABLED)")
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
        # Logs the PyJWT failure reason only — no token or credential value.
        # nosemgrep
        logger.warning("Firebase ID token rejected: invalid (INVALID_TOKEN): %s", err)
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


@lru_cache
def _get_verifier_registry() -> VerifierRegistry:
    """Build the issuer->verifier registry from settings (cached).

    Firebase is always present. The generic OIDC backend is added only
    when ``oidc_issuer`` is configured; with it empty the registry is
    Firebase-only and ``verify_token`` behaves exactly as
    ``verify_firebase_token`` did before this seam existed.
    """
    settings = get_settings()
    oidc: OidcVerifier | None = None
    if settings.oidc_issuer:
        oidc = OidcVerifier(
            issuer=settings.oidc_issuer,
            audience=settings.oidc_audience,
            jwks_uri=settings.oidc_jwks_uri,
        )
        logger.info("OIDC auth backend enabled for issuer %s", settings.oidc_issuer)
    return VerifierRegistry(firebase=FirebaseVerifier(), oidc=oidc)


def verify_token(token: str) -> VerifiedIdentity:
    """Verify a bearer ID token from any registered issuer.

    Tries each verifier in order (Firebase first, then OIDC if
    configured) and returns the first successful identity, never peeking
    at unverified claims. Firebase handles the overwhelming majority of
    tokens, so its 401 (TOKEN_EXPIRED, TOKEN_REVOKED, ...) is the error
    surfaced when no verifier accepts the token.
    """
    return _get_verifier_registry().verify(token)


def _verify_request_identity(request: Request | None, token: str) -> VerifiedIdentity:
    """Resolve a token to a VerifiedIdentity, reusing the middleware cache.

    The DatabaseSessionMiddleware pre-verifies (and caches) Firebase
    tokens during schema resolution. When that Firebase cache hits we wrap
    it directly — no second round-trip, identical to the prior behavior.
    Otherwise we route through ``verify_token`` (Firebase by default; the
    OIDC issuer when one is configured and the token's ``iss`` matches).
    """
    # Reuse an identity the middleware already verified+stashed for this
    # token (works for both Firebase and OIDC, so an OIDC token isn't
    # verified twice). Keyed to the raw token so a stale stash is ignored.
    if request is not None:
        stashed = getattr(request.state, "verified_identity", None)
        stashed_token = getattr(request.state, "verified_identity_token", None)
        if isinstance(stashed, VerifiedIdentity) and stashed_token == token:
            return stashed
    cached = _get_cached_token(request, token)
    if cached is not None:
        identity = FirebaseVerifier().verify_from_decoded(cached)
    else:
        identity = verify_token(token)
    # Stash on request.state so downstream deps that only receive the
    # decoded-claims dict (enforce_idle_session -> get_tenant_context,
    # get_current_user) can recover the provider + subject id without
    # re-verifying. Keyed to the raw token so a mismatched cache is ignored.
    if request is not None:
        request.state.verified_identity = identity
        request.state.verified_identity_token = token
    return identity


def _identity_for_decoded(
    request: Request | None, decoded_token: dict[str, Any]
) -> VerifiedIdentity:
    """Recover the VerifiedIdentity that produced ``decoded_token``.

    The MFA/idle dependency chain passes only the decoded-claims dict
    downstream. ``require_mfa`` (via ``_verify_request_identity``) has
    already stashed the full identity on ``request.state``; prefer it so
    OIDC subject ids and provider survive. Falls back to a Firebase
    interpretation of the dict for any path that bypassed that stash
    (preserves prior Firebase-only behavior).
    """
    if request is not None:
        stashed = getattr(request.state, "verified_identity", None)
        if isinstance(stashed, VerifiedIdentity) and stashed.claims is decoded_token:
            return stashed
    return FirebaseVerifier().verify_from_decoded(decoded_token)


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
    identity = _verify_request_identity(request, token)

    pablo_user_id = _resolve_pablo_user_id(
        request,
        identity.subject_id,
        identity_repo,
        create_if_missing=False,
        provider=identity.provider,
    )
    user_id_var.set(str(pablo_user_id))
    return pablo_user_id


def require_mfa(
    request: Request,
    auth_credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict[str, Any]:
    """
    Verify that the user authenticated with MFA.

    Honors the verified identity's ``mfa_satisfied`` signal — for Firebase
    that is the ``firebase.sign_in_second_factor`` claim; for an OIDC
    issuer it's the AMR/ACR step-up signal (see ``auth.providers``).
    Skipped when `settings.require_mfa` is False or in development mode.

    Returns:
        Decoded token claims

    Raises:
        HTTPException: 403 if MFA not used when required
    """
    token = auth_credentials.credentials
    identity = _verify_request_identity(request, token)
    decoded_token = identity.claims

    settings = get_settings()
    if not settings.require_mfa:
        return decoded_token
    if settings.is_development:
        logger.debug("MFA check skipped (development mode)")
        return decoded_token

    # E2E test accounts bypass MFA in non-production environments only
    if settings.e2e_test_emails and not settings.is_prod_project:
        email = decoded_token.get("email", "")
        if email in settings.e2e_test_emails and decoded_token.get("email_verified", False):
            # Logs an allow-listed E2E account's uid — an identifier, not a credential.
            # nosemgrep
            logger.warning("MFA bypassed for E2E test account: uid=%s", decoded_token.get("uid"))
            return decoded_token

    if not identity.mfa_satisfied:
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
    identity = _identity_for_decoded(request, decoded_token)
    pablo_user_id = _resolve_pablo_user_id(
        request,
        identity.subject_id,
        identity_repo,
        create_if_missing=False,
        provider=identity.provider,
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
            _await_provisioning_ready(practice_id)
            tenant_id_var.set(practice_id)
            # search_path is already set by DatabaseSessionMiddleware
            # before any dependency runs — see
            # `app.db.middleware.DatabaseSessionMiddleware._resolve_schema_from_request`.
            # We still need the active session here to set the
            # RLS user-id variable below.
            from ..db import arm_current_user_id, get_db_session

            # RLS defense-in-depth: arm app.current_user_id so row-level
            # security policies enforce per-clinician isolation within the
            # tenant schema. arm_current_user_id sets it on the open
            # transaction and stashes it in the request ContextVar so the
            # after_begin listener re-arms the GUC after any mid-request
            # commit (THERAPY-da7t lock release).
            arm_current_user_id(get_db_session(), str(pablo_user_id))
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


# Brief poll window before 503-ing on an in-flight provisioning. Five
# rounds at 200ms = 1s max wall time, comfortably under any reasonable
# request timeout while covering the typical ~1-2s DDL window. If the
# tenant is still ``in_progress`` after the budget, the caller gets a
# 503 with Retry-After so the frontend's standard retry logic kicks in.
_PROVISIONING_POLL_INTERVAL_S = 0.2
_PROVISIONING_POLL_ROUNDS = 5


def _await_provisioning_ready(practice_id: str) -> None:
    """Block until a freshly-async-provisioned tenant flips to ready.

    Paired with the background-task provisioning split added by
    THERAPY-da7t: the marketing-signup endpoint inserts the platform
    row with ``provisioning_status='in_progress'`` and schedules the
    DDL on FastAPI ``BackgroundTasks``. Until that DDL commits and the
    row flips to ``ready``, any request from the new tenant's owner
    would try to read an empty per-tenant schema and 500.

    Polls the row up to ``_PROVISIONING_POLL_ROUNDS`` times with a
    short interval. On ``ready`` returns; on ``failed`` raises 503 with
    a permanent message; on persistent ``in_progress`` raises 503 with
    Retry-After so the caller retries.
    """
    import time

    from ..db import create_standalone_session
    from ..db.platform_models import PracticeRow

    for _attempt in range(_PROVISIONING_POLL_ROUNDS):
        with create_standalone_session() as db:
            row = db.get(PracticeRow, practice_id)
        if row is None or row.provisioning_status == "ready":
            return
        if row.provisioning_status == "failed":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": {
                        "code": "TENANT_PROVISIONING_FAILED",
                        "message": (
                            "Your account is in a stuck provisioning state. Please contact support."
                        ),
                        "details": {"practice_id": practice_id},
                    }
                },
            )
        time.sleep(_PROVISIONING_POLL_INTERVAL_S)

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "error": {
                "code": "TENANT_PROVISIONING_IN_PROGRESS",
                "message": ("Your account is still being set up. Please retry in a few seconds."),
                "details": {"practice_id": practice_id},
            }
        },
        headers={"Retry-After": "5"},
    )


def _email_has_tenant_mapping(email: str) -> bool:
    """True if `email` has an `EmailTenantMappingRow` in the platform schema.

    Mirrors the implicit-allowlist fallback applied by
    `/api/ext/auth/check-allowlist` (routes/ext_auth.py): a provisioned
    tenant grants its primary email access even without an explicit
    `platform.allowed_emails` row. Keeping both gates in sync prevents
    the "passes blocking-fn but blocked on every API call" failure mode.
    """
    from ..db import create_standalone_session
    from ..db.platform_models import EmailTenantMappingRow

    with create_standalone_session() as db:
        return db.get(EmailTenantMappingRow, email.lower()) is not None


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
    identity = _verify_request_identity(request, token)
    decoded_token = identity.claims

    # Skipping MFA enrollment doesn't mean skipping the idle gate.
    # Lazy import avoids a circular service <-> idle_session import.
    from . import idle_session

    idle_session.check_and_touch(decoded_token)

    return _resolve_user(decoded_token, user_repo, allowlist_repo, identity_repo, request)


def get_session_peek_claims(
    request: Request,
    auth_credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict[str, Any]:
    """Verified token claims WITHOUT the idle-session touch or user resolution.

    Exists solely for the session-liveness endpoints (/api/auth/session):
    the read-only peek must be able to ask "is this session still alive?"
    without the act of asking refreshing the idle clock — a peek that
    touched would keep every open tab's session alive forever. Also skips
    MFA and user/allowlist resolution: the endpoints report only the
    caller's own session state (no PHI, no user data), and the idle
    controller mounts on pre-MFA onboarding screens too.

    Do NOT use for anything that returns user data or accepts user
    actions — those need ``get_current_user`` / ``get_current_user_no_mfa``.
    Registered as a pre-MFA posture marker in
    ``tests/test_route_mfa_guardrails.py``.
    """
    token = auth_credentials.credentials
    return _verify_request_identity(request, token).claims


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
    that internal id, not the provider's subject id — that's the
    decoupling that makes a future provider migration (or accepting a
    second issuer) a row insert rather than a full-schema rewrite.
    """
    identity = _identity_for_decoded(request, decoded_token)
    subject_id = identity.subject_id

    if not subject_id:
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

    # Lookup-only first: if a mapping exists, use it. If not, defer the
    # creation until after the allowlist gate so rejected users don't
    # leave stray rows.
    pablo_user_id = _resolve_pablo_user_id(
        request,
        subject_id,
        identity_repo,
        create_if_missing=False,
        provider=identity.provider,
    )

    user = user_repo.get(pablo_user_id)
    if not user:
        email = identity.email or _extract_email(decoded_token)

        # Fallback: look up email from Firebase Auth if a Firebase token
        # lacked it. Only meaningful for the Firebase provider — an OIDC
        # token carries its own verified `email` claim.
        if not email and identity.provider == "firebase":
            try:
                fb_user = firebase_auth.get_user(subject_id)
                email = (fb_user.email or "").lower()
                logger.info("Resolved email from Firebase Auth: uid=%s", subject_id)
            except Exception as exc:
                logger.warning(
                    "Could not look up email from Firebase Auth for uid=%s: %s",
                    subject_id,
                    exc,
                )

        # Reserved test-identity prefixes bypass the allowlist in non-prod
        # only (Firebase signup doesn't verify email — an attacker could
        # otherwise mint a token for an arbitrary e2etest-<hex>@pablo.health
        # address and ride this path into prod).
        from ..jobs.pentest_identity import PENTEST_EMAIL_PATTERN

        settings = get_settings()
        is_prod_project = settings.is_prod_project
        is_pentest_user = not is_prod_project and bool(email and PENTEST_EMAIL_PATTERN.match(email))
        is_e2e_user = not is_prod_project and bool(email and E2E_EMAIL_PATTERN.match(email))
        is_provisioned_tenant = bool(
            email and settings.multi_tenancy_enabled and _email_has_tenant_mapping(email)
        )
        if (
            settings.restrict_signups
            and not is_pentest_user
            and not is_e2e_user
            and not is_provisioned_tenant
            and (not email or not allowlist_repo.is_allowed(email))
        ):
            logger.warning("Blocked non-allowlisted user: uid=%s", subject_id)
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
            request,
            subject_id,
            identity_repo,
            create_if_missing=True,
            provider=identity.provider,
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
    # Arm the RLS ``app.current_user_id`` GUC for the resolved user, right
    # beside the logging var above. Both name "who this request is"; they
    # drifted apart historically — only ``get_tenant_context`` armed the RLS
    # var, so pre-MFA paths (``get_current_user_no_mfa`` -> here) and any PHI
    # route using ``get_current_user`` without ``get_tenant_context`` left it
    # unset and read/wrote zero rows under a NOBYPASSRLS role. Arming at this
    # single shared seam covers every authenticated HTTP request; off-request
    # work arms via ``tenant_db_session`` / ``run_in_tenant``. Guarded:
    # token-exchange / CLI callers pass ``request=None`` and have no
    # request-scoped session to arm.
    from ..db import _request_session, arm_current_user_id

    db_session = _request_session.get()
    if db_session is not None:
        arm_current_user_id(db_session, str(pablo_user_id))
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
    request: Request,
    user: User = Depends(get_current_user),
) -> User:
    """Verify the user's practice may perform this request.

    No-op when subscription enforcement is disabled (the single-tenant
    default), and no-op when there is no subscription record yet — a
    practice mid-provisioning is not a lapsed one.

    Otherwise the subscription's access level decides (see
    :mod:`app.auth.route_access`). Full access allows everything.
    Read-only access allows read-intent routes and refuses the rest,
    so a wound-down practice keeps view-and-export access to its
    records indefinitely. No access refuses everything behind this
    gate, which is what a subscription without an access level falls
    back to — the behavior this gate has always had.

    Raises:
        HTTPException: 403 ``SUBSCRIPTION_READONLY`` for a write under
            read-only access; 403 ``SUBSCRIPTION_INACTIVE`` when the
            subscription grants no access.
    """
    settings = get_settings()
    if not settings.is_saas:
        return user

    from ..routes.subscription import _fetch_subscription  # type: ignore[import-not-found]

    sub = _fetch_subscription(user.email, settings)
    if not sub:
        # No subscription record — might be mid-provisioning; let through
        return user

    access = resolve_access_level(sub)
    if access is AccessLevel.FULL:
        return user

    if access is AccessLevel.READ_ONLY:
        # Classify against the route's path *template*, so one entry in
        # the override table covers every patient id. Falling back to
        # the concrete path keeps this honest if a request ever reaches
        # the gate without a matched route: it simply won't hit an
        # override, and the method decides.
        route = request.scope.get("route")
        path_template = getattr(route, "path_format", request.url.path)
        if access_intent(request.method, path_template) is AccessIntent.READ:
            return user

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "SUBSCRIPTION_READONLY",
                    "message": (
                        "Your subscription has ended. Your records remain "
                        "available to view and export."
                    ),
                    "details": {
                        "status": sub.get("status"),
                        "access_level": AccessLevel.READ_ONLY.value,
                        "grace_extension_available": sub.get("grace_extension_available", False),
                    },
                }
            },
        )

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


def _cloud_tasks_backend_audience(settings: Settings) -> str:
    """The OIDC audience Cloud Tasks stamps on internal-job tokens.

    Must match what ``cloud_tasks_service.enqueue_cloud_task`` sets as the
    token audience (the backend's own URL), or verification rejects every job.
    """
    return settings.transcription_backend_callback_url or settings.app_url.replace(":3000", ":8000")


def require_cloud_tasks_invoker(request: Request) -> None:
    """Gate internal job endpoints to the Cloud-Tasks invoker service account.

    Cloud Tasks delivers jobs as an authenticated HTTP POST carrying a Google
    OIDC token signed by ``cloud-tasks-invoker@<project>`` with the backend URL
    as its audience (see ``cloud_tasks_service.enqueue_cloud_task``). Verify the
    signature, issuer, audience, and that the email is exactly that service
    account before any job runs — these routes drive privileged, tenant-scoped
    work and must never be reachable by an ordinary user token. Rejects a
    missing bearer with 401 and any other failure with 403, mirroring
    :func:`require_pentest_runner`.
    """
    settings = get_settings()
    expected_sa = f"cloud-tasks-invoker@{settings.gcp_project_id}.iam.gserviceaccount.com"
    audience = _cloud_tasks_backend_audience(settings)
    if not settings.gcp_project_id or not audience:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": {
                    "code": "CLOUD_TASKS_INVOKER_NOT_CONFIGURED",
                    "message": "Cloud Tasks invoker identity is not configured.",
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
                    "code": "CLOUD_TASKS_INVOKER_REQUIRED",
                    "message": "Missing bearer token.",
                    "details": {},
                }
            },
        )
    token = auth_header[7:]

    try:
        claims = _verify_google_oidc_token(token, audience=audience)
    except Exception as exc:
        logger.warning("Cloud Tasks invoker OIDC verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "CLOUD_TASKS_INVOKER_REQUIRED",
                    "message": "Invalid Cloud Tasks invoker token.",
                    "details": {},
                }
            },
        ) from exc

    email = str(claims.get("email", "")).lower()
    if email != expected_sa.lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "CLOUD_TASKS_INVOKER_REQUIRED",
                    "message": "Token does not belong to the Cloud Tasks invoker.",
                    "details": {},
                }
            },
        )


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


def _session_passkey_hardware_satisfied(request: Request) -> bool:
    """Whether the request's session was satisfied by a device-bound passkey.

    Reads the ``pablo_passkey`` claim the assertion-finish endpoint stamps on
    a verified passkey token. A TOTP-satisfied or synced-passkey session does
    not qualify — only a device-bound (hardware) authenticator does.
    """
    identity = getattr(request.state, "verified_identity", None)
    claims = identity.claims if isinstance(identity, VerifiedIdentity) else {}
    passkey = claims.get("pablo_passkey")
    return isinstance(passkey, dict) and passkey.get("hw") is True


def require_admin_hardware_key(
    request: Request,
    user: User = Depends(require_admin),
) -> User:
    """Admin gate that additionally requires a hardware-passkey step-up.

    Builds on ``require_admin`` (admin role + dev bypass), then — when
    ``webauthn_admin_require_hardware_key`` is enabled — requires the session
    to have been satisfied by a device-bound passkey. A phishing-resistant
    hardware key is the point of the control, so a TOTP or synced-passkey
    session is rejected even for an admin. Default-off: with the flag unset
    this is exactly ``require_admin``.
    """
    settings = get_settings()
    if not settings.webauthn_admin_require_hardware_key or settings.is_development:
        return user
    if not _session_passkey_hardware_satisfied(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "HARDWARE_KEY_REQUIRED",
                    "message": "A hardware security key is required for admin access",
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
