# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Admin API routes — user management and allowlist."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..auth.service import require_admin
from ..models import User
from ..repositories import (
    AllowlistRepository,
    UserRepository,
    get_allowlist_repository,
    get_user_repository,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])


# --- User Management Models ---


class UserListItem(BaseModel):
    """Response model for a user in the admin list."""

    id: str
    email: str
    name: str
    status: str
    is_admin: bool
    mfa_enrolled_at: str | None
    baa_accepted_at: str | None
    created_at: str


class UserListResponse(BaseModel):
    """Response for listing all users."""

    data: list[UserListItem]
    total: int


class AllowlistEntry(BaseModel):
    """Response model for an allowlist entry."""

    email: str
    added_by: str
    added_at: str


class AllowlistResponse(BaseModel):
    """Response for listing allowlisted emails."""

    data: list[AllowlistEntry]
    total: int


class AddToAllowlistRequest(BaseModel):
    """Request to add an email to the allowlist."""

    email: str = Field(min_length=3, max_length=255)


# --- User Management Endpoints ---


@router.get("/api/admin/users")
def list_users(
    _admin: User = Depends(require_admin),
    user_repo: UserRepository = Depends(get_user_repository),
) -> UserListResponse:
    """List all users with status information."""
    users = user_repo.list_all()
    items = [
        UserListItem(
            id=u.id,
            email=u.email,
            name=u.name,
            status=u.status,
            is_admin=u.is_admin,
            mfa_enrolled_at=u.mfa_enrolled_at,
            baa_accepted_at=u.baa_accepted_at,
            created_at=u.created_at,
        )
        for u in users
    ]
    return UserListResponse(data=items, total=len(items))


@router.patch("/api/admin/users/{user_id}/disable")
def disable_user(
    user_id: str,
    admin: User = Depends(require_admin),
    user_repo: UserRepository = Depends(get_user_repository),
) -> dict[str, str]:
    """Disable a user account."""
    target = user_repo.get(user_id)
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "User not found", "details": {}}},
        )
    if target.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "CANNOT_DISABLE_SELF",
                    "message": "You cannot disable your own account",
                    "details": {},
                }
            },
        )
    target.status = "disabled"
    user_repo.update(target)
    logger.info("Admin %s disabled user %s", admin.id, target.id)
    return {"message": "User disabled", "user_id": user_id}


@router.patch("/api/admin/users/{user_id}/enable")
def enable_user(
    user_id: str,
    admin: User = Depends(require_admin),
    user_repo: UserRepository = Depends(get_user_repository),
) -> dict[str, str]:
    """Re-enable a disabled user account."""
    target = user_repo.get(user_id)
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "User not found", "details": {}}},
        )
    target.status = "approved"
    user_repo.update(target)
    logger.info("Admin %s enabled user %s", admin.id, target.id)
    return {"message": "User enabled", "user_id": user_id}


# --- Allowlist Endpoints ---


@router.get("/api/admin/allowlist")
def list_allowlist(
    _admin: User = Depends(require_admin),
    allowlist_repo: AllowlistRepository = Depends(get_allowlist_repository),
) -> AllowlistResponse:
    """List all allowlisted emails."""
    entries = allowlist_repo.list_all()
    items = [
        AllowlistEntry(
            email=e.get("email", ""),
            added_by=e.get("added_by", ""),
            added_at=e.get("added_at", ""),
        )
        for e in entries
    ]
    return AllowlistResponse(data=items, total=len(items))


@router.post("/api/admin/allowlist", status_code=status.HTTP_201_CREATED)
def add_to_allowlist(
    request: AddToAllowlistRequest,
    admin: User = Depends(require_admin),
    allowlist_repo: AllowlistRepository = Depends(get_allowlist_repository),
) -> dict[str, str]:
    """Add an email to the allowlist (this IS the invitation)."""
    allowlist_repo.add(request.email, admin.id)
    logger.info("Admin %s added email to allowlist", admin.id)
    return {"message": "Email added to allowlist", "email": request.email.lower()}


@router.delete("/api/admin/allowlist/{email}")
def remove_from_allowlist(
    email: str,
    admin: User = Depends(require_admin),
    allowlist_repo: AllowlistRepository = Depends(get_allowlist_repository),
) -> dict[str, str]:
    """Remove an email from the allowlist."""
    if not allowlist_repo.remove(email):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {"code": "NOT_FOUND", "message": "Email not in allowlist", "details": {}}
            },
        )
    logger.info("Admin %s removed email from allowlist", admin.id)
    return {"message": "Email removed from allowlist", "email": email.lower()}
