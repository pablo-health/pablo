# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""
User API routes.

Implements user profile management and BAA (Business Associate Agreement) acceptance.
"""

import re
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse

from ..auth.service import get_baa_version, get_current_user, get_current_user_no_mfa
from ..models import AcceptBAARequest, BAAStatusResponse, User, UserPreferences
from ..repositories import UserRepository, get_user_repository

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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "INVALID_VERSION",
                    "message": "BAA version must be a date in YYYY-MM-DD format",
                    "details": {"version": version},
                }
            },
        )

    available = _get_available_baa_files()
    baa_path = available.get(version)
    if baa_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "NOT_FOUND",
                    "message": f"BAA version {version} not found",
                    "details": {"version": version},
                }
            },
        )

    return baa_path


@router.get("/me/status")
def get_user_status(
    user: User = Depends(get_current_user_no_mfa),
) -> dict[str, str | bool | None]:
    """
    Get current user status without requiring MFA.

    Used by dashboard layout to check if MFA enrollment is needed
    before the user has completed MFA setup.
    """
    return {
        "status": user.status,
        "mfa_enrolled_at": user.mfa_enrolled_at,
        "is_admin": user.is_admin,
        "name": user.name,
        "email": user.email,
    }


@router.post("/me/mfa-enrolled")
def record_mfa_enrollment(
    user: User = Depends(get_current_user_no_mfa),
    user_repo: UserRepository = Depends(get_user_repository),
) -> dict[str, str]:
    """
    Record that the user has completed MFA enrollment on the client side.

    Called by the frontend after successful TOTP enrollment via Firebase.
    Uses get_current_user_no_mfa since this is called immediately after
    enrolling (before the user has signed in with MFA).
    """
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    user.mfa_enrolled_at = now
    user_repo.update(user)
    return {"mfa_enrolled_at": now}


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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "BAD_REQUEST",
                    "message": "BAA must be accepted",
                    "details": {},
                }
            },
        )

    # Load the full BAA text for audit trail
    baa_path = _resolve_baa_path(request.version)
    baa_full_text = baa_path.read_text()

    # Update user with BAA acceptance
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
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
        accepted_at=now,
        version=request.version,
        current_version=request.version,
    )


@router.get("/baa/{version}", response_class=PlainTextResponse)
def get_baa_text(version: str) -> str:
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
