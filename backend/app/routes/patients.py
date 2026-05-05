# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""
Patient API routes.

Implements CRUD operations for patient management with multi-tenant isolation.
"""

import logging
import uuid
from datetime import timedelta
from enum import StrEnum
from typing import cast

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse, Response

from ..api_errors import BadRequestError, NotFoundError, ServerError
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..models import (
    AuditAction,
    CloseChartRequest,
    CreatePatientRequest,
    DeletePatientRequest,
    DeletePatientResponse,
    Patient,
    PatientListResponse,
    PatientResponse,
    UpdatePatientRequest,
    User,
)
from ..repositories import (
    NotesRepository,
    PatientRepository,
    TherapySessionRepository,
)
from ..repositories import (
    get_notes_repository as _notes_repo_factory,
)
from ..repositories import (
    get_patient_repository as _patient_repo_factory,
)
from ..repositories import (
    get_session_repository as _session_repo_factory,
)
from ..services import AuditService, ExportService, get_audit_service
from ..utcnow import utc_now

logger = logging.getLogger(__name__)


class PatientSearchField(StrEnum):
    """Valid fields for patient search."""

    FIRST_NAME = "first_name"
    LAST_NAME = "last_name"


class IncludeDeletedMode(StrEnum):
    """Soft-delete visibility for ``GET /api/patients`` (THERAPY-yg2).

    Default behavior (``include_deleted`` omitted) lists only live
    patients — soft-deleted rows are hidden by the repository's
    ``deleted_at IS NULL`` filter (THERAPY-nyb).

    ``recent`` switches the listing to the 30-day undo window: rows
    with ``deleted_at IS NOT NULL AND deleted_at > NOW() - 30 days``.
    The hard-purge cron (THERAPY-cgy) physically removes anything
    older, so this mode never returns rows that are about to vanish.
    """

    RECENT = "recent"


# THERAPY-yg2: 30-day undo window. Sized to match the soft-delete
# retention before the day-30 hard-purge cron (THERAPY-cgy). Kept here
# rather than in settings to make the contract obvious at the call site
# and impossible to widen via env vars.
_UNDO_WINDOW_DAYS = 30


router = APIRouter(prefix="/api/patients", tags=["patients"])


def get_patient_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> PatientRepository:
    """Get patient repository scoped to the tenant's database."""
    return _patient_repo_factory()


def get_therapy_session_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> TherapySessionRepository:
    """Get session repository scoped to the tenant's database."""
    return _session_repo_factory()


def get_notes_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> NotesRepository:
    """Get notes repository scoped to the tenant's database."""
    return _notes_repo_factory()


def get_export_service(
    patient_repo: PatientRepository = Depends(get_patient_repository),
    session_repo: TherapySessionRepository = Depends(get_therapy_session_repository),
    notes_repo: NotesRepository = Depends(get_notes_repository),
) -> ExportService:
    """Get export service instance."""
    return ExportService(patient_repo, session_repo, notes_repo)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_patient(
    http_request: Request,
    request: CreatePatientRequest,
    user: User = Depends(require_baa_acceptance),
    repo: PatientRepository = Depends(get_patient_repository),
    audit: AuditService = Depends(get_audit_service),
) -> PatientResponse:
    """
    Create a new patient.

    - **first_name**: Patient's first name (required)
    - **last_name**: Patient's last name (required)
    - **email**: Patient's email (optional)
    - **phone**: Patient's phone number (optional)
    - **status**: Patient status - active, inactive, or on_hold (defaults to active)
    - **date_of_birth**: Date of birth in ISO format (optional)
    - **diagnosis**: Current diagnosis (optional)
    """
    now = utc_now()

    patient = Patient(
        id=str(uuid.uuid4()),
        user_id=user.id,
        first_name=request.first_name,
        last_name=request.last_name,
        email=request.email,
        phone=request.phone,
        status=request.status,
        date_of_birth=request.date_of_birth,
        diagnosis=request.diagnosis,
        created_at=now,
        updated_at=now,
        session_count=0,
        last_session_date=None,
    )

    patient = repo.create(patient)
    audit.log_patient_action(AuditAction.PATIENT_CREATED, user, http_request, patient)
    return PatientResponse.from_patient(patient)


@router.get("")
def list_patients(
    request: Request,
    search: str | None = Query(None, description="Search by patient name"),
    search_by: PatientSearchField = Query(
        PatientSearchField.LAST_NAME, description="Search field: first_name or last_name"
    ),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    include_deleted: IncludeDeletedMode | None = Query(
        None,
        description=(
            "If 'recent', return soft-deleted patients within the 30-day undo "
            "window instead of live patients. Default lists only live patients."
        ),
    ),
    user: User = Depends(require_baa_acceptance),
    repo: PatientRepository = Depends(get_patient_repository),
    audit: AuditService = Depends(get_audit_service),
) -> PatientListResponse:
    """
    List patients for the current user with pagination.

    - **search**: Optional search term for patient name
    - **search_by**: Field to search (first_name or last_name, defaults to last_name)
    - **page**: Page number (default 1)
    - **page_size**: Items per page (default 20, max 100)
    - **include_deleted**: ``recent`` switches to the 30-day soft-delete
      undo window (THERAPY-yg2). Default lists only live patients.

    Returns patients sorted by last name, then first name.
    next_session_date is denormalized on the patient document.
    """
    if include_deleted is IncludeDeletedMode.RECENT:
        # Recently-deleted slice: search/pagination intentionally do
        # not apply — the window is bounded by 30 days of soft-deletes
        # per user, which on a solo-therapist deployment is small
        # enough that returning the full list is simpler than
        # re-implementing the search path against tombstoned rows.
        # The hard-purge cron (THERAPY-cgy) caps the upper bound.
        deleted_pairs = repo.list_recently_deleted(user.id, window_days=_UNDO_WINDOW_DAYS)
        responses = [
            PatientResponse.from_patient(
                p,
                deleted_at=stamp,
                restore_deadline=stamp + timedelta(days=_UNDO_WINDOW_DAYS),
            )
            for p, stamp in deleted_pairs
        ]
        total = len(responses)
        audit.log_patient_list(user, request, total)
        return PatientListResponse(
            data=responses,
            total=total,
            page=1,
            page_size=total,
        )

    patients, total = repo.list_by_user(
        user.id, search=search, search_by=search_by.value, page=page, page_size=page_size
    )

    responses = [PatientResponse.from_patient(p) for p in patients]

    audit.log_patient_list(user, request, total)
    return PatientListResponse(
        data=responses,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{patient_id}")
def get_patient(
    patient_id: str,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    repo: PatientRepository = Depends(get_patient_repository),
    audit: AuditService = Depends(get_audit_service),
) -> PatientResponse:
    """
    Get patient details by ID.

    - **patient_id**: The patient's unique identifier

    Returns the patient if found and belongs to the current user.
    next_session_date is denormalized on the patient document.
    """
    patient = repo.get(patient_id, user.id)

    if not patient:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})

    audit.log_patient_action(AuditAction.PATIENT_VIEWED, user, request, patient)
    return PatientResponse.from_patient(patient)


@router.patch("/{patient_id}")
def update_patient(
    patient_id: str,
    http_request: Request,
    request: UpdatePatientRequest,
    user: User = Depends(require_baa_acceptance),
    repo: PatientRepository = Depends(get_patient_repository),
    audit: AuditService = Depends(get_audit_service),
) -> PatientResponse:
    """
    Update patient information.

    - **patient_id**: The patient's unique identifier
    - **first_name**: New first name (optional)
    - **last_name**: New last name (optional)
    - **email**: New email (optional)
    - **phone**: New phone number (optional)
    - **status**: New status - active, inactive, or on_hold (optional)
    - **date_of_birth**: New date of birth (optional)
    - **diagnosis**: New diagnosis (optional)

    Only provided fields will be updated.
    """
    patient = repo.get(patient_id, user.id)

    if not patient:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})

    # Track which fields changed for audit (names only — values would be PHI).
    changed_fields: list[str] = []

    if request.first_name is not None:
        changed_fields.append("first_name")
        patient.first_name = request.first_name
    if request.last_name is not None:
        changed_fields.append("last_name")
        patient.last_name = request.last_name
    if request.email is not None:
        changed_fields.append("email")
        patient.email = request.email
    if request.phone is not None:
        changed_fields.append("phone")
        patient.phone = request.phone
    if request.status is not None:
        changed_fields.append("status")
        patient.status = request.status
    if request.date_of_birth is not None:
        changed_fields.append("date_of_birth")
        patient.date_of_birth = request.date_of_birth
    if request.diagnosis is not None:
        changed_fields.append("diagnosis")
        patient.diagnosis = request.diagnosis

    patient = repo.update(patient)
    audit.log_patient_action(
        AuditAction.PATIENT_UPDATED,
        user,
        http_request,
        patient,
        changes={"changed_fields": changed_fields},
    )
    return PatientResponse.from_patient(patient)


@router.delete("/{patient_id}")
def delete_patient(
    patient_id: str,
    http_request: Request,
    body: DeletePatientRequest,
    user: User = Depends(require_baa_acceptance),
    repo: PatientRepository = Depends(get_patient_repository),
    audit: AuditService = Depends(get_audit_service),
) -> DeletePatientResponse:
    """
    Delete a patient and all associated sessions.

    - **patient_id**: The patient's unique identifier
    - **acknowledged_retention_obligation** (body, required): Must be
      ``true``. The user is attesting they have met their professional
      / state-law retention obligations for this record before
      destructive delete proceeds (THERAPY-9ig). The attestation is
      audit-logged.

    This operation cascades to soft-delete all sessions and notes for
    this patient.
    """
    # THERAPY-9ig: gate destructive delete on an explicit retention
    # attestation. Pydantic guarantees the field is present and a bool
    # (a missing field yields 422); we only need to enforce that the
    # user actually checked the box.
    if not body.acknowledged_retention_obligation:
        raise BadRequestError(
            "You must acknowledge your professional retention obligation "
            "before deleting this patient.",
            {"field": "acknowledged_retention_obligation"},
            code="RETENTION_ATTESTATION_REQUIRED",
        )

    patient = repo.get(patient_id, user.id)

    if not patient:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})

    session_count = patient.session_count

    # Atomicity (THERAPY-nyb): both the soft-delete UPDATE and the audit
    # INSERT must commit together or not at all. The request-scoped
    # SQLAlchemy session in DatabaseSessionMiddleware wraps the whole
    # request in one transaction; an exception from either step rolls
    # both back. We do the delete first, then the audit — that way an
    # audit-write failure (raised from AuditService._persist) propagates
    # out of this handler, the middleware catches it, calls
    # session.rollback(), and the patient row's deleted_at goes back to
    # NULL. If we audited first and the delete then failed silently
    # (returning False), we'd commit an audit row referencing a still-
    # live patient — the split state we're explicitly avoiding.
    deleted = repo.delete(patient_id, user.id)
    if not deleted:
        raise ServerError("Failed to delete patient")

    # PHI-free attestation marker on the audit row (THERAPY-9ig). The
    # value is a boolean literal — no free text, no PHI. Per
    # AuditLogEntry.changes contract this is exactly the shape allowed.
    audit.log_patient_action(
        AuditAction.PATIENT_DELETED,
        user,
        http_request,
        patient,
        changes={"attestation": True},
    )

    session_word = "session" if session_count == 1 else "sessions"
    return DeletePatientResponse(
        message=f"Patient and {session_count} {session_word} deleted successfully"
    )


@router.post("/{patient_id}/restore")
def restore_patient(
    patient_id: str,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    repo: PatientRepository = Depends(get_patient_repository),
    audit: AuditService = Depends(get_audit_service),
) -> PatientResponse:
    """Reverse a soft-delete inside the 30-day undo window (THERAPY-yg2).

    Returns 404 if the patient is not soft-deleted, doesn't belong to
    the caller, or its ``deleted_at`` is past the 30-day window — the
    same status as a regular missing-patient lookup so we don't leak
    the lifecycle state of rows the caller can no longer act on.

    Cascade matches the soft-delete cascade: therapy sessions and
    notes that were tombstoned together with the patient come back
    together. Sessions / notes that were independently soft-deleted
    earlier stay tombstoned. Session numbers are preserved — the
    next-number generator ignores ``deleted_at`` so a restored
    session keeps its original number (THERAPY-nyb).
    """
    restored = repo.restore(patient_id, user.id, window_days=_UNDO_WINDOW_DAYS)
    if restored is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})

    audit.log_patient_action(AuditAction.PATIENT_RESTORED, user, request, restored)
    return PatientResponse.from_patient(restored)


@router.post("/{patient_id}/close-chart")
def close_chart(
    patient_id: str,
    http_request: Request,
    body: CloseChartRequest,
    user: User = Depends(require_baa_acceptance),
    repo: PatientRepository = Depends(get_patient_repository),
    audit: AuditService = Depends(get_audit_service),
) -> PatientResponse:
    """
    Close a patient's chart (THERAPY-hek).

    Marks the patient's care episode as ended without removing the
    record. Closure is **orthogonal** to soft-delete: the row stays
    visible in list/get responses, and the day-30 hard-purge clock
    (THERAPY-cgy) is **not** advanced.

    - **patient_id**: The patient's unique identifier
    - **closure_reason** (body, optional): Free-form reason. Stored on
      the patient row but never copied into audit logs (free text could
      be PHI-adjacent).
    """
    patient = repo.close_chart(patient_id, user.id, body.closure_reason)
    if patient is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})

    # Audit changes payload is PHI-free: only flags whether a reason was
    # supplied, never the reason text itself.
    audit.log_patient_action(
        AuditAction.CHART_CLOSED,
        user,
        http_request,
        patient,
        changes={"closure_reason_provided": body.closure_reason is not None},
    )
    return PatientResponse.from_patient(patient)


@router.post("/{patient_id}/reopen-chart")
def reopen_chart(
    patient_id: str,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    repo: PatientRepository = Depends(get_patient_repository),
    audit: AuditService = Depends(get_audit_service),
) -> PatientResponse:
    """
    Reopen a previously-closed chart (THERAPY-hek).

    Clears ``chart_closed_at`` and ``chart_closure_reason``. Audit-only
    record of the transition.
    """
    patient = repo.reopen_chart(patient_id, user.id)
    if patient is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})

    audit.log_patient_action(
        AuditAction.CHART_REOPENED,
        user,
        http_request,
        patient,
    )
    return PatientResponse.from_patient(patient)


@router.get("/{patient_id}/export")
def export_patient_data(
    patient_id: str,
    request: Request,
    format: str = Query("json", description="Export format: json or pdf"),
    user: User = Depends(require_baa_acceptance),
    repo: PatientRepository = Depends(get_patient_repository),
    export_service: ExportService = Depends(get_export_service),
    audit: AuditService = Depends(get_audit_service),
) -> Response:
    """
    Export complete patient data for HIPAA Right to Access (§ 164.524).

    - **patient_id**: The patient's unique identifier
    - **format**: Export format - 'json' or 'pdf' (defaults to 'json')

    Returns all patient data including demographics, sessions, transcripts, and SOAP notes.
    """
    # Get patient for audit log
    patient = repo.get(patient_id, user.id)
    if not patient:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})

    try:
        export_data = export_service.get_patient_export_data(patient_id, user.id, format)
    except ValueError as e:
        logger.error("Patient export failed: %s", e)
        raise BadRequestError(
            "Invalid export request. Check the format parameter.",
            {"format": format},
            code="INVALID_REQUEST",
        ) from e

    audit.log_patient_action(
        AuditAction.PATIENT_EXPORTED,
        user,
        request,
        patient,
        changes={"export_format": format},
    )

    # Return PDF as file download
    if format == "pdf":
        return Response(
            content=cast("bytes", export_data["content"]),
            media_type=cast("str", export_data["content_type"]),
            headers={"Content-Disposition": f'attachment; filename="{export_data["filename"]}"'},
        )

    # Return JSON directly
    return JSONResponse(content=export_data)
