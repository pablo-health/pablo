# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""
User API routes.

Implements user profile management and BAA (Business Associate Agreement) acceptance.
"""

import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from firebase_admin import auth as firebase_auth
from pydantic import BaseModel

from ..api_errors import BadRequestError, NotFoundError
from ..auth.service import get_baa_version, get_current_user, get_current_user_no_mfa
from ..models import AcceptBAARequest, BAAStatusResponse, User, UserPreferences
from ..repositories import UserRepository, get_user_repository
from ..services import AuditService, get_audit_service
from ..utcnow import utc_now, utc_now_iso

router = APIRouter(prefix="/api/users", tags=["users"])

BAA_DIR = (Path(__file__).parent.parent.parent / "baa").resolve()
BAA_VERSION_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _get_available_baa_files() -> dict[str, Path]:
    """Scan BAA directory and return a map of version → resolved path."""
    result: dict[str, Path] = {}
    for path in BAA_DIR.glob("BAA-*.md"):
        version = path.stem.removeprefix("BAA-")
        if BAA_VERSION_PATTERN.match(version):
            result[version] = path.resolve()
    return result


def _resolve_baa_path(version: str) -> Path:
    """Validate a BAA version string and return the resolved file path.

    Raises HTTPException 400/404 if the version format is invalid or
    no matching BAA file exists on disk.
    """
    if not BAA_VERSION_PATTERN.match(version):
        raise BadRequestError(
            "BAA version must be a date in YYYY-MM-DD format",
            {"version": version},
            code="INVALID_VERSION",
        )

    available = _get_available_baa_files()
    baa_path = available.get(version)
    if baa_path is None:
        raise NotFoundError(f"BAA version {version} not found", {"version": version})

    return baa_path


@router.get("/me/status")
def get_user_status(
    user: User = Depends(get_current_user_no_mfa),
) -> dict:
    """
    Get current user status without requiring MFA.

    Used by dashboard layout and companion app to check MFA enrollment
    and subscription/trial status.
    """
    from ..settings import get_settings

    result: dict = {
        "status": user.status,
        "mfa_enrolled_at": user.mfa_enrolled_at,
        "is_platform_admin": user.is_platform_admin,
        "name": user.name,
        "email": user.email,
    }

    # Include subscription/trial info for SaaS editions
    settings = get_settings()
    if settings.is_saas:
        from .subscription import _get_subscription_info  # type: ignore[import-not-found]

        sub_info = _get_subscription_info(user.email, settings)
        if sub_info:
            result["subscription"] = sub_info

    return result


@router.post("/me/mfa-enrolled")
def record_mfa_enrollment(
    user: User = Depends(get_current_user_no_mfa),
    user_repo: UserRepository = Depends(get_user_repository),
) -> dict[str, str]:
    """
    Record that the user has completed MFA enrollment.

    Called by the frontend after successful TOTP enrollment via Firebase.
    Verifies enrollment server-side against the Firebase Admin SDK — the
    client's claim is not sufficient on its own, or an attacker could
    mint a bogus ``mfa_enrolled_at`` timestamp and poison compliance
    metrics.
    """
    try:
        fb_user = firebase_auth.get_user(str(user.id))
    except firebase_auth.UserNotFoundError as exc:
        raise NotFoundError("Firebase user not found") from exc

    enrolled_factors = getattr(fb_user, "multi_factor", None)
    enrolled = list(enrolled_factors.enrolled_factors) if enrolled_factors else []
    if not any(getattr(f, "factor_id", "") == "totp" for f in enrolled):
        raise BadRequestError(
            "No TOTP factor enrolled for this user in Firebase",
            code="MFA_NOT_ENROLLED",
        )

    user.mfa_enrolled_at = utc_now()
    user_repo.update(user)
    return {"mfa_enrolled_at": utc_now_iso()}


@router.get("/me")
def get_current_user_profile(
    user: User = Depends(get_current_user),
) -> User:
    """
    Get current user profile.

    Returns the authenticated user's profile information.
    """
    return user


@router.get("/me/baa-status")
def get_baa_status(
    user: User = Depends(get_current_user_no_mfa),
    current_version: str = Depends(get_baa_version),
) -> BAAStatusResponse:
    """
    Get BAA acceptance status for the current user.

    Returns:
        - accepted: Whether user has accepted any version of BAA
        - accepted_at: Timestamp of acceptance (if accepted)
        - version: Version they accepted (if accepted)
        - current_version: The current BAA version
    """
    return BAAStatusResponse(
        accepted=user.baa_accepted_at is not None,
        accepted_at=user.baa_accepted_at,
        version=user.baa_version,
        current_version=current_version,
    )


@router.post("/me/accept-baa")
def accept_baa(
    request: AcceptBAARequest,
    user: User = Depends(get_current_user_no_mfa),
    user_repo: UserRepository = Depends(get_user_repository),
) -> BAAStatusResponse:
    """
    Accept the Business Associate Agreement.

    This endpoint records the user's acceptance of the BAA with their
    professional credentials and practice information for HIPAA compliance.

    Required fields:
    - legal_name: User's full legal name
    - license_number: Professional license number
    - license_state: Two-letter state code where licensed
    - business_address: Complete business address
    - practice_name: Practice/business name (optional)
    - version: BAA version being accepted
    """
    if not request.accepted:
        raise BadRequestError("BAA must be accepted")

    # Load the full BAA text for audit trail
    baa_path = _resolve_baa_path(request.version)
    baa_full_text = baa_path.read_text()

    # Update user with BAA acceptance
    now = utc_now()
    user.baa_accepted_at = now
    user.baa_version = request.version
    user.baa_legal_name = request.legal_name
    user.baa_license_number = request.license_number
    user.baa_license_state = request.license_state
    user.baa_practice_name = request.practice_name
    user.baa_business_address = request.business_address
    user.baa_full_text = baa_full_text

    user_repo.update(user)

    return BAAStatusResponse(
        accepted=True,
        accepted_at=utc_now(),
        version=request.version,
        current_version=request.version,
    )


@router.get("/baa/{version}", response_class=PlainTextResponse)
def get_baa_text(
    version: str,
    _user: User = Depends(get_current_user_no_mfa),
) -> str:
    """
    Get the full text of a specific BAA version.

    This endpoint serves the Business Associate Agreement text in markdown format.

    Args:
        version: The BAA version identifier (e.g., "2024-01-01")

    Returns:
        The full BAA text in markdown format
    """
    baa_path = _resolve_baa_path(version)
    return baa_path.read_text()


@router.get("/baa", response_class=PlainTextResponse)
def get_current_baa(
    current_version: str = Depends(get_baa_version),
) -> str:
    """
    Get the current BAA version text.

    Returns the most recent Business Associate Agreement text in markdown format.
    """
    return get_baa_text(current_version)


@router.get("/me/preferences")
def get_preferences(
    user: User = Depends(get_current_user),
    user_repo: UserRepository = Depends(get_user_repository),
) -> UserPreferences:
    """Fetch user preferences. Returns defaults if never saved."""
    return user_repo.get_preferences(user.id)


@router.put("/me/preferences")
def save_preferences(
    prefs: UserPreferences,
    user: User = Depends(get_current_user),
    user_repo: UserRepository = Depends(get_user_repository),
) -> UserPreferences:
    """Save user preferences (full replace)."""
    return user_repo.save_preferences(user.id, prefs)


class AuditLogItem(BaseModel):
    """One row in the user's own audit trail.

    Intentionally omits ``user_id`` (implicit — it's you), ``changes``
    (the only field that could carry PHI-adjacent data despite the
    field-name guard, and users don't need it), and ``expires_at``
    (internal retention metadata).
    """

    id: str
    timestamp: str
    action: str
    resource_type: str
    resource_id: str
    patient_id: str | None = None
    session_id: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None


class AuditLogResponse(BaseModel):
    data: list[AuditLogItem]
    limit: int


AUDIT_LOG_MAX_LIMIT = 500


@router.get("/me/audit-log", response_model=AuditLogResponse)
def list_my_audit_log(
    request: Request,
    since: datetime | None = Query(
        None,
        description=(
            "ISO-8601 timestamp — return only rows strictly after this. "
            "Use the timestamp of the last row from a prior call to page forward."
        ),
    ),
    limit: int = Query(100, ge=1, le=AUDIT_LOG_MAX_LIMIT),
    user: User = Depends(get_current_user),
    audit: AuditService = Depends(get_audit_service),
) -> AuditLogResponse:
    """Return the caller's own audit rows, newest first.

    Scoped to ``user_id = current user`` — never accepts a user_id
    parameter. Tenant boundary is enforced by the session's
    search_path (set by ``DatabaseSessionMiddleware``). The access
    itself is audited (``self_audit_viewed``) so reads of the audit
    stream are themselves traceable.
    """
    entries = audit.list_for_user(user_id=user.id, since=since, limit=limit)
    audit.log_self_audit_view(
        user=user, request=request, returned_count=len(entries)
    )
    return AuditLogResponse(
        data=[
            AuditLogItem(
                id=e.id,
                timestamp=e.timestamp,
                action=e.action,
                resource_type=e.resource_type,
                resource_id=e.resource_id,
                patient_id=e.patient_id,
                session_id=e.session_id,
                ip_address=e.ip_address,
                user_agent=e.user_agent,
            )
            for e in entries
        ],
        limit=limit,
    )
