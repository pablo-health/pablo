# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Outcome measures API routes.

Two routers are registered in ``main.py``:

* ``outcome_measures_router`` — ``/api/outcome-measures/{id}`` (get, delete).
* ``patient_outcome_measures_router`` — ``/api/patients/{patient_id}/outcome-measures``
  (create, list).

Patient access is enforced by :func:`require_baa_acceptance` (authentication
gate) plus a repository-layer check via ``has_patient_access`` — the same
two-layer pattern used by the notes router.  No separate RLS policy is
created; schema-per-tenant isolation plus the app-layer check is sufficient,
matching the notes table design.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query, status

from ..api_errors import BadRequestError, NotFoundError
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..repositories import get_outcome_measure_repository as _repo_factory
from .instruments import InstrumentValidationError
from .schemas import CreateOutcomeMeasureRequest, OutcomeMeasureListResponse, OutcomeMeasureResponse
from .service import OutcomeMeasureNotFoundError, OutcomeMeasureService, UnknownInstrumentError

if TYPE_CHECKING:
    from ..models import User
    from ..repositories.outcome_measure import OutcomeMeasureRepository

logger = logging.getLogger(__name__)

outcome_measures_router = APIRouter(
    prefix="/api/outcome-measures",
    tags=["outcome-measures"],
)
patient_outcome_measures_router = APIRouter(
    prefix="/api/patients",
    tags=["outcome-measures"],
)


# ---------------------------------------------------------------------------
# Dependency factories
# ---------------------------------------------------------------------------


def get_outcome_measure_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> OutcomeMeasureRepository:
    """Return a tenant-scoped outcome measure repository."""
    return _repo_factory()  # type: ignore[no-any-return]  # factory is no-untyped-def for ruff compat


def get_outcome_measure_service(
    repo: OutcomeMeasureRepository = Depends(get_outcome_measure_repository),
) -> OutcomeMeasureService:
    return OutcomeMeasureService(repo)


# ---------------------------------------------------------------------------
# /api/outcome-measures endpoints
# ---------------------------------------------------------------------------


@outcome_measures_router.get(
    "/{measure_id}",
    response_model=OutcomeMeasureResponse,
)
def get_outcome_measure(
    measure_id: str,
    user: User = Depends(require_baa_acceptance),
    service: OutcomeMeasureService = Depends(get_outcome_measure_service),
) -> OutcomeMeasureResponse:
    """Fetch a single outcome measure by id."""
    try:
        return service.get(measure_id, user.id)
    except OutcomeMeasureNotFoundError as exc:
        raise NotFoundError("Outcome measure not found", {"measure_id": measure_id}) from exc


@outcome_measures_router.delete(
    "/{measure_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_outcome_measure(
    measure_id: str,
    user: User = Depends(require_baa_acceptance),
    service: OutcomeMeasureService = Depends(get_outcome_measure_service),
) -> None:
    """Soft-delete an outcome measure (sets deleted_at; preserves the audit row)."""
    try:
        service.soft_delete(measure_id, user.id)
    except OutcomeMeasureNotFoundError as exc:
        raise NotFoundError("Outcome measure not found", {"measure_id": measure_id}) from exc


# ---------------------------------------------------------------------------
# /api/patients/{patient_id}/outcome-measures endpoints
# ---------------------------------------------------------------------------


@patient_outcome_measures_router.post(
    "/{patient_id}/outcome-measures",
    status_code=status.HTTP_201_CREATED,
    response_model=OutcomeMeasureResponse,
)
def create_outcome_measure(
    patient_id: str,
    request: CreateOutcomeMeasureRequest,
    user: User = Depends(require_baa_acceptance),
    service: OutcomeMeasureService = Depends(get_outcome_measure_service),
) -> OutcomeMeasureResponse:
    """Record a scored clinical instrument result for a patient.

    Validates the instrument + item_scores via the instrument registry when
    item_scores are provided.  Computes ``total_score``, ``is_complete``, and
    the severity label automatically.  Accepts an explicit ``total_score``
    when item-level detail is unavailable.
    """
    try:
        return service.create(patient_id, request, user.id)
    except OutcomeMeasureNotFoundError as exc:
        # Denied write to a patient the caller can't access. Surface as 404
        # (not 403) to avoid leaking patient existence — mirrors reads.
        raise NotFoundError("Patient not found", {"patient_id": patient_id}) from exc
    except UnknownInstrumentError as exc:
        raise BadRequestError(
            str(exc),
            {"instrument": request.instrument},
            code="UNKNOWN_INSTRUMENT",
        ) from exc
    except InstrumentValidationError as exc:
        raise BadRequestError(
            str(exc),
            {"instrument": request.instrument},
            code="INVALID_ITEM_SCORES",
        ) from exc
    except ValueError as exc:
        raise BadRequestError(str(exc), {}, code="INVALID_REQUEST") from exc


@patient_outcome_measures_router.get(
    "/{patient_id}/outcome-measures",
    response_model=OutcomeMeasureListResponse,
)
def list_outcome_measures(
    patient_id: str,
    instrument: str | None = Query(default=None, description="Filter by instrument code"),
    user: User = Depends(require_baa_acceptance),
    service: OutcomeMeasureService = Depends(get_outcome_measure_service),
) -> OutcomeMeasureListResponse:
    """List outcome measures for a patient, ordered by administered_at ascending.

    Use ``?instrument=phq9`` to filter to a single instrument (e.g. for a
    trend chart).  Returns an empty list when the caller has no access grant
    for the patient — consistent with the notes list endpoint behaviour.
    """
    measures = service.list_for_patient(patient_id, user.id, instrument=instrument)
    return OutcomeMeasureListResponse(data=measures, total=len(measures))
