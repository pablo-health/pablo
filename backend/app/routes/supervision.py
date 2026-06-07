# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Supervision-relationship and accrued-hours routes.

Clinicians who work under physician delegation, NP collaborative practice
agreements, PA supervision orders, or pre-licensure supervision track those
relationships here. Each relationship can optionally link to a
``compliance_items`` review item so the review deadline is picked up by the
existing reminder machinery.

All routes are user-scoped: a clinician only ever reads and writes their own
supervision relationships and hour entries.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..api_errors import BadRequestError, NotFoundError
from ..auth.service import get_current_user, get_tenant_context
from ..models import User
from ..repositories import get_supervision_repository
from ..repositories.postgres.supervision import (
    PostgresSupervisionRepository,
    SupervisionHours,
    SupervisionRelationship,
)
from ..utcnow import utc_now

# The ``supervision_*`` tables live in the tenant schema with RLS keyed on
# ``app.current_user_id``. Declaring ``get_tenant_context`` as a router-level
# dependency ensures the GUC is armed before any handler touches the repo,
# matching the pattern used by compliance.py.
router = APIRouter(
    prefix="/api/supervision",
    tags=["supervision"],
    dependencies=[Depends(get_tenant_context)],
)

ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

RepoDep = Annotated[PostgresSupervisionRepository, Depends(get_supervision_repository)]
UserDep = Annotated[User, Depends(get_current_user)]


# ---------------------------------------------------------------------------
# Request / response models — relationships
# ---------------------------------------------------------------------------


class SupervisionRelationshipPayload(BaseModel):
    """Fields accepted on create and update."""

    relationship_type: str = Field(min_length=1, max_length=80)
    supervisor_name: str = Field(min_length=1, max_length=255)
    supervisor_credential: str | None = Field(default=None, max_length=50)
    supervisor_dea: str | None = Field(default=None, max_length=20)
    supervisor_license: str | None = Field(default=None, max_length=50)
    state: str | None = Field(default=None, max_length=2)
    effective_date: str | None = Field(default=None, max_length=10)
    review_cadence_days: int | None = Field(default=None, ge=1, le=3650)
    next_review_date: str | None = Field(default=None, max_length=10)
    authority_ref: str | None = Field(default=None, max_length=500)
    status: str = Field(default="active", min_length=1, max_length=20)
    notes: str | None = Field(default=None, max_length=2000)


class SupervisionRelationshipResponse(BaseModel):
    id: str
    relationship_type: str
    supervisor_name: str
    supervisor_credential: str | None
    supervisor_dea: str | None
    supervisor_license: str | None
    state: str | None
    effective_date: str | None
    review_cadence_days: int | None
    next_review_date: str | None
    authority_ref: str | None
    status: str
    notes: str | None
    compliance_item_id: str | None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Request / response models — hours
# ---------------------------------------------------------------------------


class SupervisionHoursPayload(BaseModel):
    """Fields accepted when logging supervision hours."""

    logged_date: str = Field(max_length=10)
    hours: Decimal = Field(gt=Decimal("0"), le=Decimal("24"))
    kind: str = Field(min_length=1, max_length=50)
    supervisor: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=2000)


class SupervisionHoursResponse(BaseModel):
    id: str
    supervision_relationship_id: str
    logged_date: str
    hours: Decimal
    kind: str
    supervisor: str | None
    notes: str | None
    created_at: str
    updated_at: str


class AccrualResponse(BaseModel):
    """Summarises total hours logged for a relationship.

    ``total_hours`` is the sum of all logged entries. Setting an accrual
    target (e.g. 1 000 hours toward licensure) is left to the client/user's
    notes field — the server exposes the sum and lets the UI or the
    relationship's ``notes`` carry the target.
    """

    supervision_relationship_id: str
    total_hours: Decimal
    entry_count: int
    entries: list[SupervisionHoursResponse]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_date(value: str | None, field: str) -> None:
    if value is not None and not ISO_DATE_PATTERN.match(value):
        raise BadRequestError(
            f"{field} must be ISO date YYYY-MM-DD",
            {field: value},
            code="INVALID_DATE",
        )


def _relationship_to_response(rel: SupervisionRelationship) -> SupervisionRelationshipResponse:
    return SupervisionRelationshipResponse(
        id=rel.id,
        relationship_type=rel.relationship_type,
        supervisor_name=rel.supervisor_name,
        supervisor_credential=rel.supervisor_credential,
        supervisor_dea=rel.supervisor_dea,
        supervisor_license=rel.supervisor_license,
        state=rel.state,
        effective_date=rel.effective_date,
        review_cadence_days=rel.review_cadence_days,
        next_review_date=rel.next_review_date,
        authority_ref=rel.authority_ref,
        status=rel.status,
        notes=rel.notes,
        compliance_item_id=rel.compliance_item_id,
        created_at=rel.created_at.isoformat(),
        updated_at=rel.updated_at.isoformat(),
    )


def _hours_to_response(entry: SupervisionHours) -> SupervisionHoursResponse:
    return SupervisionHoursResponse(
        id=entry.id,
        supervision_relationship_id=entry.supervision_relationship_id,
        logged_date=entry.logged_date,
        hours=entry.hours,
        kind=entry.kind,
        supervisor=entry.supervisor,
        notes=entry.notes,
        created_at=entry.created_at.isoformat(),
        updated_at=entry.updated_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# Relationship routes
# ---------------------------------------------------------------------------


@router.get("", response_model=list[SupervisionRelationshipResponse])
def list_supervision_relationships(
    user: UserDep,
    repo: RepoDep,
) -> list[SupervisionRelationshipResponse]:
    """List the caller's supervision relationships, oldest first."""
    return [_relationship_to_response(r) for r in repo.list_by_user(user.id)]


@router.post("", response_model=SupervisionRelationshipResponse, status_code=201)
def create_supervision_relationship(
    payload: SupervisionRelationshipPayload,
    user: UserDep,
    repo: RepoDep,
) -> SupervisionRelationshipResponse:
    """Create a supervision relationship.

    When ``next_review_date`` is provided a linked ``compliance_items``
    review item is created in the same transaction so the deadline rides
    the existing reminder machinery. The label defaults to
    ``"<supervisor_name> supervision review"`` and the item type is
    ``"supervision_review"``.
    """
    _validate_date(payload.effective_date, "effective_date")
    _validate_date(payload.next_review_date, "next_review_date")

    now = utc_now()
    rel = SupervisionRelationship(
        id=str(uuid.uuid4()),
        user_id=user.id,
        compliance_item_id=None,
        relationship_type=payload.relationship_type,
        supervisor_name=payload.supervisor_name,
        supervisor_credential=payload.supervisor_credential,
        supervisor_dea=payload.supervisor_dea,
        supervisor_license=payload.supervisor_license,
        state=payload.state,
        effective_date=payload.effective_date,
        review_cadence_days=payload.review_cadence_days,
        next_review_date=payload.next_review_date,
        authority_ref=payload.authority_ref,
        status=payload.status,
        notes=payload.notes,
        created_at=now,
        updated_at=now,
    )

    # Build a review-item label only when a review date is set so the repo
    # creates and links the compliance item in the same flush.
    review_label: str | None = None
    if rel.next_review_date is not None:
        review_label = f"{payload.supervisor_name} supervision review"

    created = repo.create_relationship(rel, review_item_label=review_label)
    return _relationship_to_response(created)


@router.put("/{relationship_id}", response_model=SupervisionRelationshipResponse)
def update_supervision_relationship(
    relationship_id: str,
    payload: SupervisionRelationshipPayload,
    user: UserDep,
    repo: RepoDep,
) -> SupervisionRelationshipResponse:
    """Update an existing supervision relationship (full replace of editable fields)."""
    _validate_date(payload.effective_date, "effective_date")
    _validate_date(payload.next_review_date, "next_review_date")

    existing = repo.get(relationship_id, user.id)
    if existing is None:
        raise NotFoundError("Supervision relationship not found")

    existing.relationship_type = payload.relationship_type
    existing.supervisor_name = payload.supervisor_name
    existing.supervisor_credential = payload.supervisor_credential
    existing.supervisor_dea = payload.supervisor_dea
    existing.supervisor_license = payload.supervisor_license
    existing.state = payload.state
    existing.effective_date = payload.effective_date
    existing.review_cadence_days = payload.review_cadence_days
    existing.next_review_date = payload.next_review_date
    existing.authority_ref = payload.authority_ref
    existing.status = payload.status
    existing.notes = payload.notes
    existing.updated_at = utc_now()

    return _relationship_to_response(repo.update(existing))


@router.delete("/{relationship_id}", status_code=204)
def delete_supervision_relationship(
    relationship_id: str,
    user: UserDep,
    repo: RepoDep,
) -> None:
    """Delete a supervision relationship and all its linked hour entries."""
    if not repo.delete(relationship_id, user.id):
        raise NotFoundError("Supervision relationship not found")


# ---------------------------------------------------------------------------
# Hours sub-routes
# ---------------------------------------------------------------------------


@router.get("/{relationship_id}/hours", response_model=AccrualResponse)
def list_supervision_hours(
    relationship_id: str,
    user: UserDep,
    repo: RepoDep,
) -> AccrualResponse:
    """List accrued-hour entries for a relationship and return the running total.

    Returns 404 when the relationship does not exist or belongs to another user.
    The ``total_hours`` field sums all logged entries — callers can compare it
    against any board-mandated target they've stored in the relationship's
    ``notes`` field.
    """
    # Ownership check: the relationship must belong to the caller.
    rel = repo.get(relationship_id, user.id)
    if rel is None:
        raise NotFoundError("Supervision relationship not found")

    entries = repo.list_hours(relationship_id, user.id)
    total = sum((e.hours for e in entries), Decimal("0"))
    return AccrualResponse(
        supervision_relationship_id=relationship_id,
        total_hours=total,
        entry_count=len(entries),
        entries=[_hours_to_response(e) for e in entries],
    )


@router.post("/{relationship_id}/hours", response_model=SupervisionHoursResponse, status_code=201)
def add_supervision_hours(
    relationship_id: str,
    payload: SupervisionHoursPayload,
    user: UserDep,
    repo: RepoDep,
) -> SupervisionHoursResponse:
    """Log an accrued-hours entry against a supervision relationship.

    Returns 404 when the relationship does not exist or belongs to another user.
    """
    _validate_date(payload.logged_date, "logged_date")

    # Ownership check before writing.
    rel = repo.get(relationship_id, user.id)
    if rel is None:
        raise NotFoundError("Supervision relationship not found")

    now = utc_now()
    entry = SupervisionHours(
        id=str(uuid.uuid4()),
        supervision_relationship_id=relationship_id,
        user_id=user.id,
        logged_date=payload.logged_date,
        hours=payload.hours,
        kind=payload.kind,
        supervisor=payload.supervisor,
        notes=payload.notes,
        created_at=now,
        updated_at=now,
    )
    created = repo.add_hours(entry)
    return _hours_to_response(created)
