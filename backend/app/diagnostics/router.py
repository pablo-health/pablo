# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Diagnostics API routes.

Three routers, mounted in ``main.py``:

* ``diagnostic_definitions_router`` — ``/api/diagnostic-definitions`` (list, for
  rendering the criterion form).
* ``diagnostic_assessments_router`` — ``/api/diagnostic-assessments/{id}``
  (get, delete).
* ``patient_diagnostic_assessments_router`` —
  ``/api/patients/{patient_id}/diagnostic-assessments`` (create, list).

Patient access is enforced by ``require_baa_acceptance`` plus the repository's
``has_patient_access`` check — the same two-layer pattern as notes and outcome
measures.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query, status

from ..api_errors import BadRequestError, NotFoundError
from ..auth.service import require_baa_acceptance
from ..repositories import (
    get_diagnostic_assessment_repository,
    get_diagnostic_definition_provider,
)
from .schemas import (
    CreateDiagnosticAssessmentRequest,
    DiagnosticAssessmentListResponse,
    DiagnosticAssessmentResponse,
    DiagnosticDefinitionListResponse,
    PrescribingSupportResponse,
)
from .service import (
    DiagnosticAssessmentNotFoundError,
    DiagnosticService,
    InvalidCodeError,
    InvalidResponsesError,
    UnknownDefinitionError,
)

if TYPE_CHECKING:
    from ..models import User
    from ..repositories.diagnostic_assessment import DiagnosticAssessmentRepository
    from .definition_provider import DefinitionProvider

logger = logging.getLogger(__name__)

diagnostic_definitions_router = APIRouter(
    prefix="/api/diagnostic-definitions", tags=["diagnostics"]
)
diagnostic_assessments_router = APIRouter(
    prefix="/api/diagnostic-assessments", tags=["diagnostics"]
)
patient_diagnostic_assessments_router = APIRouter(prefix="/api/patients", tags=["diagnostics"])


def get_diagnostic_service(
    repo: DiagnosticAssessmentRepository = Depends(get_diagnostic_assessment_repository),
    provider: DefinitionProvider = Depends(get_diagnostic_definition_provider),
) -> DiagnosticService:
    return DiagnosticService(repo, provider)


@diagnostic_definitions_router.get("", response_model=DiagnosticDefinitionListResponse)
def list_definitions(
    _user: User = Depends(require_baa_acceptance),
    service: DiagnosticService = Depends(get_diagnostic_service),
) -> DiagnosticDefinitionListResponse:
    """List the available diagnostic definitions (for rendering the form).

    The auth dependency gates access; its value is unused (definition listing
    is not patient-scoped).
    """
    defs = service.list_definitions()
    return DiagnosticDefinitionListResponse(data=defs, total=len(defs))


@diagnostic_definitions_router.get(
    "/{code}/prescribing-support", response_model=PrescribingSupportResponse
)
def get_prescribing_support(
    code: str,
    _user: User = Depends(require_baa_acceptance),
    service: DiagnosticService = Depends(get_diagnostic_service),
) -> PrescribingSupportResponse:
    """Return a definition's optional prescribing-support reference data.

    Decision-support reference material a prescriber may consult — differentials
    to weigh, the medication rationale, and jurisdiction-configurable
    safeguards. Empty when the definition carries none (the default). The auth
    dependency gates access; its value is unused (definitions are not
    patient-scoped).
    """
    try:
        return service.get_prescribing_support(code)
    except UnknownDefinitionError as exc:
        raise NotFoundError("Diagnostic definition not found", {"code": code}) from exc


@diagnostic_assessments_router.get("/{assessment_id}", response_model=DiagnosticAssessmentResponse)
def get_assessment(
    assessment_id: str,
    user: User = Depends(require_baa_acceptance),
    service: DiagnosticService = Depends(get_diagnostic_service),
) -> DiagnosticAssessmentResponse:
    try:
        return service.get(assessment_id, user.id)
    except DiagnosticAssessmentNotFoundError as exc:
        raise NotFoundError(
            "Diagnostic assessment not found", {"assessment_id": assessment_id}
        ) from exc


@diagnostic_assessments_router.delete(
    "/{assessment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_assessment(
    assessment_id: str,
    user: User = Depends(require_baa_acceptance),
    service: DiagnosticService = Depends(get_diagnostic_service),
) -> None:
    """Soft-delete an assessment (preserves the audit row)."""
    try:
        service.soft_delete(assessment_id, user.id)
    except DiagnosticAssessmentNotFoundError as exc:
        raise NotFoundError(
            "Diagnostic assessment not found", {"assessment_id": assessment_id}
        ) from exc


@patient_diagnostic_assessments_router.post(
    "/{patient_id}/diagnostic-assessments",
    status_code=status.HTTP_201_CREATED,
    response_model=DiagnosticAssessmentResponse,
)
def create_assessment(
    patient_id: str,
    request: CreateDiagnosticAssessmentRequest,
    user: User = Depends(require_baa_acceptance),
    service: DiagnosticService = Depends(get_diagnostic_service),
) -> DiagnosticAssessmentResponse:
    """Record a structured diagnostic determination for a patient."""
    try:
        return service.create(patient_id, request, user.id)
    except DiagnosticAssessmentNotFoundError as exc:
        # Denied write to an inaccessible patient — surface as 404, not 403,
        # to avoid leaking patient existence (mirrors reads).
        raise NotFoundError("Patient not found", {"patient_id": patient_id}) from exc
    except UnknownDefinitionError as exc:
        raise BadRequestError(
            str(exc), {"instrument": request.instrument}, code="UNKNOWN_DEFINITION"
        ) from exc
    except InvalidResponsesError as exc:
        raise BadRequestError(str(exc), {}, code="INVALID_RESPONSES") from exc
    except InvalidCodeError as exc:
        raise BadRequestError(
            str(exc), {"determined_icd10": request.determined_icd10}, code="INVALID_CODE"
        ) from exc


@patient_diagnostic_assessments_router.get(
    "/{patient_id}/diagnostic-assessments",
    response_model=DiagnosticAssessmentListResponse,
)
def list_assessments(
    patient_id: str,
    instrument: str | None = Query(default=None, description="Filter by definition code"),
    user: User = Depends(require_baa_acceptance),
    service: DiagnosticService = Depends(get_diagnostic_service),
) -> DiagnosticAssessmentListResponse:
    """List a patient's diagnostic assessments, ordered by assessed_at ascending."""
    rows = service.list_for_patient(patient_id, user.id, instrument=instrument)
    return DiagnosticAssessmentListResponse(data=rows, total=len(rows))
