# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Problem-list API: a client's diagnoses, as the chart records them.

* ``GET    /api/patients/{patient_id}/problems`` — the list, in order.
* ``POST   /api/patients/{patient_id}/problems`` — add one, from the chart or
  from a note (``source_note_id``). A problem already listed is refused with
  ``409 PROBLEM_ALREADY_LISTED`` and the id of the row it matches.
* ``PATCH  /api/patients/{patient_id}/problems/{problem_id}`` — edit, resolve,
  reactivate.
* ``PUT    /api/patients/{patient_id}/problems/order`` — reorder.
* ``DELETE /api/patients/{patient_id}/problems/{problem_id}`` — remove one
  entered in error. Resolving is a status, not a removal.

Every route loads the patient first, which is the access check (an
inaccessible patient is a 404), and audits by id only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request, status

from ..api_errors import BadRequestError, ConflictError, NotFoundError
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..models import AuditAction, Patient, User
from ..problems.dependencies import get_problem_service
from ..problems.schemas import (
    AddProblemRequest,
    ProblemListResponse,
    ProblemResponse,
    ReorderProblemsRequest,
    UpdateProblemRequest,
)
from ..problems.service import (
    DuplicateProblemError,
    InvalidProblemOrderError,
    ProblemNotFoundError,
    ProblemService,
)
from ..repositories import PatientRepository
from ..repositories import (
    get_diagnostic_assessment_repository as _diagnostic_repo_factory,
)
from ..services import AuditService, get_audit_service
from .patients import get_patient_repository

if TYPE_CHECKING:
    from ..problems.models import Problem
    from ..repositories.diagnostic_assessment import DiagnosticAssessmentRepository

router = APIRouter(prefix="/api/patients", tags=["problems"])


def get_problem_diagnostic_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> DiagnosticAssessmentRepository:
    return _diagnostic_repo_factory()


def _patient_or_404(repo: PatientRepository, patient_id: str, user: User) -> Patient:
    patient = repo.get(patient_id, user.id)
    if patient is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})
    return patient


def _list_response(problems: list[Problem]) -> ProblemListResponse:
    return ProblemListResponse(
        data=[ProblemResponse.from_problem(p) for p in problems], total=len(problems)
    )


def _list_change(change: str, problem_id: str | None = None) -> dict[str, object]:
    """Audit detail for a list write: what happened and to which row, by id."""
    changes: dict[str, object] = {"changed_fields": ["problem_list"], "problem_list": change}
    if problem_id is not None:
        changes["problem_id"] = problem_id
    return changes


@router.get("/{patient_id}/problems", response_model=ProblemListResponse)
def list_problems(
    patient_id: str,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_patient_repository),
    service: ProblemService = Depends(get_problem_service),
    audit: AuditService = Depends(get_audit_service),
) -> ProblemListResponse:
    patient = _patient_or_404(patients, patient_id, user)
    problems = service.problems(patient.id)
    audit.log_patient_action(
        AuditAction.PATIENT_VIEWED,
        user,
        http_request,
        patient,
        changes={"viewed": "problem_list"},
    )
    return _list_response(problems)


@router.post(
    "/{patient_id}/problems",
    status_code=status.HTTP_201_CREATED,
    response_model=ProblemResponse,
)
def add_problem(
    patient_id: str,
    body: AddProblemRequest,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_patient_repository),
    service: ProblemService = Depends(get_problem_service),
    assessments: DiagnosticAssessmentRepository = Depends(get_problem_diagnostic_repository),
    audit: AuditService = Depends(get_audit_service),
) -> ProblemResponse:
    patient = _patient_or_404(patients, patient_id, user)

    assessment: dict[str, object] | None = None
    if body.diagnostic_assessment_id is not None:
        assessment = assessments.get(body.diagnostic_assessment_id, user.id)
        if (
            assessment is None
            or assessment.get("deleted_at") is not None
            or str(assessment["patient_id"]) != patient.id
        ):
            raise NotFoundError(
                "Diagnostic assessment not found",
                {"diagnostic_assessment_id": body.diagnostic_assessment_id},
            )

    try:
        problem = service.add(patient.id, user.id, body)
    except DuplicateProblemError as exc:
        raise ConflictError(
            "That problem is already on the list.",
            {"problem_id": exc.existing.id},
            code="PROBLEM_ALREADY_LISTED",
        ) from exc

    if assessment is not None:
        assessment["problem_id"] = problem.id
        assessments.update(assessment, user.id)

    audit.log_patient_action(
        AuditAction.PATIENT_UPDATED,
        user,
        http_request,
        patient,
        changes=_list_change("added", problem.id),
    )
    return ProblemResponse.from_problem(problem)


@router.put("/{patient_id}/problems/order", response_model=ProblemListResponse)
def reorder_problems(
    patient_id: str,
    body: ReorderProblemsRequest,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_patient_repository),
    service: ProblemService = Depends(get_problem_service),
    audit: AuditService = Depends(get_audit_service),
) -> ProblemListResponse:
    patient = _patient_or_404(patients, patient_id, user)
    try:
        problems = service.reorder(patient.id, body.problem_ids)
    except InvalidProblemOrderError as exc:
        raise BadRequestError(str(exc), {"patient_id": patient_id}) from exc
    audit.log_patient_action(
        AuditAction.PATIENT_UPDATED,
        user,
        http_request,
        patient,
        changes=_list_change("reordered"),
    )
    return _list_response(problems)


@router.patch("/{patient_id}/problems/{problem_id}", response_model=ProblemResponse)
def update_problem(
    patient_id: str,
    problem_id: str,
    body: UpdateProblemRequest,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_patient_repository),
    service: ProblemService = Depends(get_problem_service),
    audit: AuditService = Depends(get_audit_service),
) -> ProblemResponse:
    patient = _patient_or_404(patients, patient_id, user)
    try:
        problem = service.update(patient.id, problem_id, body)
    except ProblemNotFoundError as exc:
        raise NotFoundError("Problem not found", {"problem_id": problem_id}) from exc
    audit.log_patient_action(
        AuditAction.PATIENT_UPDATED,
        user,
        http_request,
        patient,
        changes=_list_change("updated", problem.id),
    )
    return ProblemResponse.from_problem(problem)


@router.delete("/{patient_id}/problems/{problem_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_problem(
    patient_id: str,
    problem_id: str,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_patient_repository),
    service: ProblemService = Depends(get_problem_service),
    audit: AuditService = Depends(get_audit_service),
) -> None:
    patient = _patient_or_404(patients, patient_id, user)
    try:
        service.remove(patient.id, problem_id)
    except ProblemNotFoundError as exc:
        raise NotFoundError("Problem not found", {"problem_id": problem_id}) from exc
    audit.log_patient_action(
        AuditAction.PATIENT_UPDATED,
        user,
        http_request,
        patient,
        changes=_list_change("removed", problem_id),
    )
