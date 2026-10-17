# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Where a client came from: the import that brought them in, if one did.

``GET /api/patients/{patient_id}/import-origin`` — the latest import still
standing that landed on this client, as the chart header shows it: which
system, which run, and whether the client was created by it, updated by it
or merged into a client who was already here. ``origin`` is null for a client
added by hand, and for one whose import has since been undone.

Read-only, and grant-scoped the way every chart read is: the client is looked
up through the repository, so a client this clinician holds no grant on is
"not found", never a 403 that confirms the id. Reading audits one patient
view, like the chart itself.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ..auth.service import require_baa_acceptance
from ..db import get_db_session
from ..migration.ledger import RecordState, origin_of
from ..models import AuditAction, User
from ..repositories import PatientRepository
from ..services import AuditService, get_audit_service
from ._deps import patient_not_found, visible_patient
from .patients import get_patient_repository

router = APIRouter(prefix="/api/patients", tags=["migration"])


class ImportOriginOut(BaseModel):
    run_id: str
    source_system: str
    started_at: datetime
    finished_at: datetime | None
    state: RecordState


class ImportOriginResponse(BaseModel):
    origin: ImportOriginOut | None


@router.get("/{patient_id}/import-origin", response_model=ImportOriginResponse)
def get_patient_import_origin(
    patient_id: str,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_patient_repository),
    audit: AuditService = Depends(get_audit_service),
) -> ImportOriginResponse:
    patient = visible_patient(patients, patient_id, user.id, patient_not_found)
    origin = origin_of(get_db_session(), "patients", patient.id)
    audit.log_patient_action(
        AuditAction.PATIENT_VIEWED,
        user,
        http_request,
        patient,
        changes={"viewed": "import_origin"},
    )
    return ImportOriginResponse(
        origin=ImportOriginOut(
            run_id=origin.run_id,
            source_system=origin.source_system,
            started_at=origin.started_at,
            finished_at=origin.finished_at,
            state=origin.state,
        )
        if origin is not None
        else None
    )
