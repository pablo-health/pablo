# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Admin API routes."""

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..auth.service import require_admin
from ..database import get_admin_firestore_client, get_firestore_client, get_tenant_firestore_client
from ..models import AuditAction, SOAPNoteModel, User
from ..repositories import (
    AllowlistRepository,
    FirestorePatientRepository,
    UserRepository,
    get_allowlist_repository,
    get_user_repository,
)
from ..services import AuditService, get_audit_service
from ..settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])

def _get_all_session_databases() -> list[tuple[str | None, Any]]:
    """Return Firestore clients for all databases that contain therapy sessions.

    In single-tenant mode, returns just the default database.
    In multi-tenant mode, returns each active tenant's database.

    Returns list of (tenant_id | None, firestore_client) tuples.
    """
    settings = get_settings()
    if not settings.multi_tenancy_enabled:
        return [(None, get_firestore_client())]

    admin_db = get_admin_firestore_client()
    databases = []
    for doc in admin_db.collection("tenants").stream():
        data = doc.to_dict()
        if not data or data.get("status") != "active":
            continue
        tenant_id = data["tenant_id"]
        db_name = data["firestore_database"]
        databases.append((tenant_id, get_tenant_firestore_client(db_name)))
    return databases

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

class ExportQueueItemResponse(BaseModel):
    """Response model for a single export queue item."""

    id: str
    user_id: str
    patient_name: str
    session_date: str
    session_number: int
    quality_rating: int | None
    redacted_transcript: str | None
    redacted_soap_note: SOAPNoteModel | None
    export_status: str
    export_queued_at: str | None
    finalized_at: str | None

class ExportQueueListResponse(BaseModel):
    """Response model for export queue list."""

    data: list[ExportQueueItemResponse]
    total: int

class ExportActionRequest(BaseModel):
    """Request model for export queue action."""

    action: str = Field(pattern="^(approve|skip|flag)$")
    reason: str | None = None

@router.get("/api/admin/export-queue")
def list_export_queue(
    request: Request,
    user: User = Depends(require_admin),
    audit: AuditService = Depends(get_audit_service),
) -> ExportQueueListResponse:
    """
    List sessions pending export review.

    Returns all sessions with export_status=pending_review across all users.
    Requires admin privileges (bypassed in dev mode).

    Returns:
        List of queued sessions with redacted content for review
    """
    sessions = []
    for _tenant_id, db in _get_all_session_databases():
        patient_repo = FirestorePatientRepository(db)
        sessions_ref = db.collection("therapy_sessions")
        query = sessions_ref.where(
            "export_status", "==", "pending_review"
        ).order_by("export_queued_at")

        for doc in query.stream():
            data = doc.to_dict()
            if not data:
                continue

            # Fetch patient name
            patient_id = data.get("patient_id")
            user_id = data.get("user_id")
            patient = None
            if patient_id and user_id:
                patient = patient_repo.get(patient_id, user_id)

            patient_name = patient.formal_name if patient else "Unknown Patient"

            # Parse redacted SOAP note if present
            redacted_soap = None
            if data.get("redacted_soap_note"):
                soap_data = data["redacted_soap_note"]
                redacted_soap = SOAPNoteModel(
                    subjective=soap_data["subjective"],
                    objective=soap_data["objective"],
                    assessment=soap_data["assessment"],
                    plan=soap_data["plan"],
                )

            sessions.append(
                ExportQueueItemResponse(
                    id=data["id"],
                    user_id=data["user_id"],
                    patient_name=patient_name,
                    session_date=data["session_date"],
                    session_number=data["session_number"],
                    quality_rating=data.get("quality_rating"),
                    redacted_transcript=data.get("redacted_transcript"),
                    redacted_soap_note=redacted_soap,
                    export_status=data["export_status"],
                    export_queued_at=data.get("export_queued_at"),
                    finalized_at=data.get("finalized_at"),
                )
            )

    # Sort globally by export_queued_at across all tenants
    sessions.sort(key=lambda s: s.export_queued_at or "")

    audit.log_admin_action(
        AuditAction.EXPORT_QUEUE_VIEWED,
        user,
        request,
        changes={"queue_count": len(sessions)},
    )

    return ExportQueueListResponse(data=sessions, total=len(sessions))

@router.post("/api/admin/export-queue/{session_id}/action")
def perform_export_action(
    session_id: str,
    http_request: Request,
    request: ExportActionRequest,
    user: User = Depends(require_admin),
    audit: AuditService = Depends(get_audit_service),
) -> dict[str, str]:
    """
    Perform action on queued export session.

    Actions:
    - approve: Set status to "approved" (ready for export)
    - skip: Set status to "skipped" (remove from queue)
    - flag: Set status to "skipped" with reason (PII concern)

    Args:
        session_id: Session ID to act on
        request: Action to perform

    Returns:
        Success message with new status
    """
    # Find session across all databases (multi-tenant aware)
    session_ref = None
    session_data = None
    for _tenant_id, db in _get_all_session_databases():
        ref = db.collection("therapy_sessions").document(session_id)
        doc = ref.get()
        if doc.exists:
            session_ref = ref
            session_data = doc.to_dict()
            break

    if session_ref is None or session_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "NOT_FOUND",
                    "message": "Session not found",
                    "details": {"session_id": session_id},
                }
            },
        )

    # Validate action
    action = request.action
    if action not in ["approve", "skip", "flag"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "INVALID_ACTION",
                    "message": f"Invalid action: {action}",
                    "details": {"valid_actions": ["approve", "skip", "flag"]},
                }
            },
        )

    # Determine new status
    new_status = "approved" if action == "approve" else "skipped"

    # Update session
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    update_data = {
        "export_status": new_status,
        "export_reviewed_at": now,
        "export_reviewed_by": user.id,
    }

    session_ref.update(update_data)

    audit.log_admin_action(
        AuditAction.EXPORT_ACTION_TAKEN,
        user,
        http_request,
        resource_id=session_id,
        changes={"action": action, "new_status": new_status},
    )

    return {
        "message": f"Session {action}d successfully",
        "session_id": session_id,
        "export_status": new_status,
    }

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
