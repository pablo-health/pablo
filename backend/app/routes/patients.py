# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""
Patient API routes.

Implements CRUD operations for patient management with multi-tenant isolation.
"""

import logging
import uuid
from enum import StrEnum
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, Response

from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..models import (
    AuditAction,
    CreatePatientRequest,
    DeletePatientResponse,
    Patient,
    PatientListResponse,
    PatientResponse,
    UpdatePatientRequest,
    User,
)
from ..repositories import (
    PatientRepository,
    TherapySessionRepository,
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


def get_export_service(
    patient_repo: PatientRepository = Depends(get_patient_repository),
    session_repo: TherapySessionRepository = Depends(get_therapy_session_repository),
) -> ExportService:
    """Get export service instance."""
    return ExportService(patient_repo, session_repo)


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

    Returns patients sorted by last name, then first name.
    next_session_date is denormalized on the patient document.
    """
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "NOT_FOUND",
                    "message": "Patient not found",
                    "details": {"patient_id": patient_id},
                }
            },
        )

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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "NOT_FOUND",
                    "message": "Patient not found",
                    "details": {"patient_id": patient_id},
                }
            },
        )

    # Track changes for audit log
    changes: dict[str, dict[str, str | None]] = {}

    # Update only provided fields
    if request.first_name is not None:
        changes["first_name"] = {"old": patient.first_name, "new": request.first_name}
        patient.first_name = request.first_name
    if request.last_name is not None:
        changes["last_name"] = {"old": patient.last_name, "new": request.last_name}
        patient.last_name = request.last_name
    if request.email is not None:
        changes["email"] = {"old": patient.email, "new": request.email}
        patient.email = request.email
    if request.phone is not None:
        changes["phone"] = {"old": patient.phone, "new": request.phone}
        patient.phone = request.phone
    if request.status is not None:
        changes["status"] = {"old": patient.status, "new": request.status}
        patient.status = request.status
    if request.date_of_birth is not None:
        changes["date_of_birth"] = {"old": patient.date_of_birth, "new": request.date_of_birth}
        patient.date_of_birth = request.date_of_birth
    if request.diagnosis is not None:
        changes["diagnosis"] = {"old": patient.diagnosis, "new": request.diagnosis}
        patient.diagnosis = request.diagnosis

    patient = repo.update(patient)
    audit.log_patient_action(
        AuditAction.PATIENT_UPDATED, user, http_request, patient, changes=changes
    )
    return PatientResponse.from_patient(patient)


@router.delete("/{patient_id}")
def delete_patient(
    patient_id: str,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    repo: PatientRepository = Depends(get_patient_repository),
    audit: AuditService = Depends(get_audit_service),
) -> DeletePatientResponse:
    """
    Delete a patient and all associated sessions.

    - **patient_id**: The patient's unique identifier

    This operation cascades to delete all sessions for this patient.
    """
    patient = repo.get(patient_id, user.id)

    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "NOT_FOUND",
                    "message": "Patient not found",
                    "details": {"patient_id": patient_id},
                }
            },
        )

    # Log before deletion (patient won't exist after)
    audit.log_patient_action(AuditAction.PATIENT_DELETED, user, request, patient)

    session_count = patient.session_count
    deleted = repo.delete(patient_id, user.id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "Failed to delete patient",
                    "details": {},
                }
            },
        )

    session_word = "session" if session_count == 1 else "sessions"
    return DeletePatientResponse(
        message=f"Patient and {session_count} {session_word} deleted successfully"
    )


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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "NOT_FOUND",
                    "message": "Patient not found",
                    "details": {"patient_id": patient_id},
                }
            },
        )

    try:
        export_data = export_service.get_patient_export_data(patient_id, user.id, format)
    except ValueError as e:
        logger.error("Patient export failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "Invalid export request. Check the format parameter.",
                    "details": {"format": format},
                }
            },
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
