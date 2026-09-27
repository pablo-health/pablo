# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Admin API routes — user management and allowlist."""

import logging
from collections.abc import Iterator
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ..api_errors import BadRequestError, NotFoundError
from ..auth.service import TenantContext, get_tenant_context, require_admin_hardware_key
from ..db import get_db_session
from ..models import Patient, User
from ..models.audit import AuditAction, ResourceType
from ..models.export import ExportOptions
from ..repositories import (
    AllowlistRepository,
    UserRepository,
    get_allowlist_repository,
    get_user_repository,
)
from ..repositories.patient import PatientRepository
from ..services import AuditService, ExportService, get_audit_service
from ..services.practice_export_service import PracticeExportState, stream_practice_archive
from ..services.tenant_export_service import (
    TenantExportState,
    audit_log_csv,
    stream_tenant_archive,
    visible_counts_payload,
)
from ..utcnow import utc_now
from .patients import get_export_service, get_patient_repository, record_exported_files

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])


class UserListItem(BaseModel):
    """Response model for a user in the admin list."""

    id: str
    email: str
    name: str
    status: str
    is_platform_admin: bool
    mfa_enrolled_at: datetime | None
    baa_accepted_at: datetime | None
    created_at: datetime


class UserListResponse(BaseModel):
    """Response for listing all users."""

    data: list[UserListItem]
    total: int


class AllowlistEntry(BaseModel):
    """Response model for an allowlist entry."""

    email: str
    added_by: str
    added_at: datetime | None


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
    _admin: User = Depends(require_admin_hardware_key),
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
            is_platform_admin=u.is_platform_admin,
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
    admin: User = Depends(require_admin_hardware_key),
    user_repo: UserRepository = Depends(get_user_repository),
) -> dict[str, str]:
    """Disable a user account."""
    target = user_repo.get(user_id)
    if not target:
        raise NotFoundError("User not found")
    if target.id == admin.id:
        raise BadRequestError("You cannot disable your own account", code="CANNOT_DISABLE_SELF")
    target.status = "disabled"
    user_repo.update(target)
    logger.info("Admin %s disabled user %s", admin.id, target.id)
    return {"message": "User disabled", "user_id": user_id}


@router.patch("/api/admin/users/{user_id}/enable")
def enable_user(
    user_id: str,
    admin: User = Depends(require_admin_hardware_key),
    user_repo: UserRepository = Depends(get_user_repository),
) -> dict[str, str]:
    """Re-enable a disabled user account."""
    target = user_repo.get(user_id)
    if not target:
        raise NotFoundError("User not found")
    target.status = "approved"
    user_repo.update(target)
    logger.info("Admin %s enabled user %s", admin.id, target.id)
    return {"message": "User enabled", "user_id": user_id}


# --- Allowlist Endpoints ---


@router.get("/api/admin/allowlist")
def list_allowlist(
    _admin: User = Depends(require_admin_hardware_key),
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
    admin: User = Depends(require_admin_hardware_key),
    ctx: TenantContext = Depends(get_tenant_context),
    allowlist_repo: AllowlistRepository = Depends(get_allowlist_repository),
) -> dict[str, str]:
    """Add an email to the allowlist (this IS the invitation).

    The invitation is into the inviting admin's own practice — that is
    what makes it an invitation rather than a bare grant, and it is what
    the invitee's first login resolves through.
    """
    if not ctx.practice_id:
        # Refusing beats writing a grant that cannot resolve: the invitee
        # would sign in successfully and land nowhere.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This account is not attached to a practice, so it cannot invite anyone.",
        )
    allowlist_repo.add(request.email, admin.id, practice_id=ctx.practice_id)
    logger.info("Admin %s invited an email into practice %s", admin.id, ctx.practice_id)
    return {"message": "Email added to allowlist", "email": request.email.lower()}


# --- Tenant-wide export (HIPAA Right to Access for the practice) ---


class TenantExportRequest(BaseModel):
    """Request body for POST /api/admin/tenant-export.

    The default is the practice archive: every chart the caller can open,
    each as the archive the chart's own Export builds, with the practice-wide
    CSV files beside them. ``include_transcripts`` and
    ``include_psychotherapy_notes`` are the chart export's two options,
    applied to every archive; left off, no transcript and no restricted note
    ships.

    ``raw`` keeps the earlier table dump (a tar.gz of four tables, in
    ``format``) for one release, for a caller built against it. It goes
    after that. ``include_audio`` is accepted on that path and ignored, as
    it always was.
    """

    raw: bool = False
    format: Literal["json", "csv"] = "json"
    include_audio: bool = False
    include_transcripts: bool = False
    include_psychotherapy_notes: bool = False


@router.post("/api/admin/tenant-export")
def tenant_export(
    body: TenantExportRequest,
    request: Request,
    admin: User = Depends(require_admin_hardware_key),
    db: Session = Depends(get_db_session),
    audit: AuditService = Depends(get_audit_service),
    patients: PatientRepository = Depends(get_patient_repository),
    export_service: ExportService = Depends(get_export_service),
) -> StreamingResponse:
    """Stream the practice as one ZIP, or with ``raw`` the earlier table dump.

    Admin only. The archive holds ``patients/<id>/`` with each chart's own
    export archive, ``clients.csv`` and ``appointments.csv`` for the whole
    practice, ``audit_log.csv``, and ``manifest.json`` with every file's
    checksum. What is in it is what this admin's session can read: the
    charts they hold a grant on, and within each, what row-level security
    lets them see.

    Audit rows are emitted from a ``BackgroundTask`` that Starlette runs
    after the response body is fully sent: one TENANT_EXPORTED row for the
    archive, and for each chart in it the same rows the chart's own export
    writes. If the client disconnects mid-stream or a build raises, the
    holder stays empty and nothing is written — successful exports are
    audited; aborted ones are not.
    """
    if not body.raw:
        return _practice_archive(body, request, admin, db, audit, patients, export_service)

    state = TenantExportState()

    def _emit_audit() -> None:
        if state.summary is None:
            return
        audit.log(
            AuditAction.TENANT_EXPORTED,
            admin,
            request,
            resource_type=ResourceType.TENANT_EXPORT,
            resource_id="archive",
            changes={
                "format": body.format,
                "include_audio": False,
                "size_bytes": state.summary.size_bytes,
                "partial_possible": True,
                "counts": visible_counts_payload(state.summary.counts),
                "include_psychotherapy_notes": state.summary.include_psychotherapy_notes,
                "psychotherapy_notes_included": state.summary.psychotherapy_notes_included,
            },
        )
        logger.info(
            "Tenant export streamed by admin %s: format=%s size_bytes=%d counts=%s",
            admin.id,
            body.format,
            state.summary.size_bytes,
            state.summary.counts,
        )

    stream = stream_tenant_archive(
        db,
        export_format=body.format,
        include_psychotherapy_notes=body.include_psychotherapy_notes,
        state=state,
    )

    return StreamingResponse(
        stream,
        media_type="application/gzip",
        headers={
            "Content-Disposition": 'attachment; filename="tenant-export.tar.gz"',
            "Cache-Control": "no-store",
        },
        background=BackgroundTask(_emit_audit),
    )


#: How many charts the practice export reads per page while it streams.
_EXPORT_PAGE_SIZE = 100


def _every_patient(patients: PatientRepository, user_id: str) -> Iterator[Patient]:
    """Each chart the caller holds a grant on, a page at a time, as the list screen orders them."""
    page = 1
    seen = 0
    while True:
        rows, total = patients.list_by_user(user_id, page=page, page_size=_EXPORT_PAGE_SIZE)
        yield from rows
        seen += len(rows)
        if not rows or seen >= total:
            return
        page += 1


def _practice_archive(
    body: TenantExportRequest,
    request: Request,
    admin: User,
    db: Session,
    audit: AuditService,
    patients: PatientRepository,
    export_service: ExportService,
) -> StreamingResponse:
    options = ExportOptions(
        include_transcripts=body.include_transcripts,
        include_psychotherapy_notes=body.include_psychotherapy_notes,
    )
    state = PracticeExportState()

    def build(patient_id: str) -> dict[str, Any]:
        return export_service.get_patient_export_data(
            patient_id,
            admin.id,
            "zip",
            include_transcripts=options.include_transcripts,
            include_psychotherapy_notes=options.include_psychotherapy_notes,
        )

    def _emit_audit() -> None:
        if state.summary is None:
            return
        audit.log(
            AuditAction.TENANT_EXPORTED,
            admin,
            request,
            resource_type=ResourceType.TENANT_EXPORT,
            resource_id="archive",
            changes={
                "format": "zip",
                "size_bytes": state.summary.size_bytes,
                "patients": state.summary.patients,
                "files": state.summary.files,
                "partial_possible": True,
                "include_transcripts": options.include_transcripts,
                "include_psychotherapy_notes": options.include_psychotherapy_notes,
            },
        )
        for patient, exported in state.exported:
            audit.log_patient_action(
                AuditAction.PATIENT_EXPORTED,
                admin,
                request,
                patient,
                changes={
                    "export_format": "zip",
                    "include_transcripts": options.include_transcripts,
                    "include_psychotherapy_notes": options.include_psychotherapy_notes,
                },
            )
            record_exported_files(audit, admin, request, patient, exported)
        logger.info(
            "Practice export streamed by admin %s: patients=%d size_bytes=%d",
            admin.id,
            state.summary.patients,
            state.summary.size_bytes,
        )

    stream = stream_practice_archive(
        patients=_every_patient(patients, admin.id),
        build=build,
        audit_log=lambda: audit_log_csv(db),
        options=options,
        exported_at=utc_now(),
        state=state,
    )
    return StreamingResponse(
        stream,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="practice-export.zip"',
            "Cache-Control": "no-store",
        },
        background=BackgroundTask(_emit_audit),
    )


# --- User Management Models ---


@router.delete("/api/admin/allowlist/{email}")
def remove_from_allowlist(
    email: str,
    admin: User = Depends(require_admin_hardware_key),
    allowlist_repo: AllowlistRepository = Depends(get_allowlist_repository),
) -> dict[str, str]:
    """Remove an email from the allowlist."""
    if not allowlist_repo.remove(email):
        raise NotFoundError("Email not in allowlist")
    logger.info("Admin %s removed email from allowlist", admin.id)
    return {"message": "Email removed from allowlist", "email": email.lower()}
