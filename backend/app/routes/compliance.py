# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Compliance reminder routes.

Solo therapists track their own license renewal, malpractice insurance,
CAQH attestation, HIPAA training, and NPI here. These items are the
clinician's own credentials, not patient PHI, so the routes do not feed
the audit log.
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..api_errors import BadRequestError, NotFoundError
from ..auth.service import get_current_user
from ..models import User
from ..repositories import get_compliance_item_repository
from ..repositories.postgres.compliance_item import (
    ComplianceItem,
    PostgresComplianceItemRepository,
)
from ..utcnow import utc_now

router = APIRouter(prefix="/api/compliance", tags=["compliance"])

ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Known item types — clients use these to render localized labels and the
# correct cadence hint. New types can be added without a migration.
ALLOWED_ITEM_TYPES: frozenset[str] = frozenset(
    {
        "license",
        "liability_insurance",
        "caqh_attestation",
        "hipaa_training",
        "npi",
        "other",
    }
)


class ComplianceItemPayload(BaseModel):
    item_type: str = Field(min_length=1, max_length=50)
    label: str = Field(min_length=1, max_length=255)
    due_date: str | None = Field(default=None, max_length=10)
    notes: str | None = Field(default=None, max_length=2000)


class ComplianceItemResponse(BaseModel):
    id: str
    item_type: str
    label: str
    due_date: str | None
    notes: str | None
    completed_at: str | None
    created_at: str
    updated_at: str


def _validate(payload: ComplianceItemPayload) -> None:
    if payload.item_type not in ALLOWED_ITEM_TYPES:
        raise BadRequestError(
            f"Unknown item_type '{payload.item_type}'",
            {"allowed": sorted(ALLOWED_ITEM_TYPES)},
            code="UNKNOWN_ITEM_TYPE",
        )
    if payload.due_date is not None and not ISO_DATE_PATTERN.match(payload.due_date):
        raise BadRequestError(
            "due_date must be ISO date YYYY-MM-DD",
            {"due_date": payload.due_date},
            code="INVALID_DATE",
        )


def _to_response(item: ComplianceItem) -> ComplianceItemResponse:
    return ComplianceItemResponse(
        id=item.id,
        item_type=item.item_type,
        label=item.label,
        due_date=item.due_date,
        notes=item.notes,
        completed_at=item.completed_at.isoformat() if item.completed_at else None,
        created_at=item.created_at.isoformat(),
        updated_at=item.updated_at.isoformat(),
    )


RepoDep = Annotated[
    PostgresComplianceItemRepository, Depends(get_compliance_item_repository)
]
UserDep = Annotated[User, Depends(get_current_user)]


@router.get("", response_model=list[ComplianceItemResponse])
def list_compliance_items(user: UserDep, repo: RepoDep) -> list[ComplianceItemResponse]:
    """List the caller's compliance items, oldest first."""
    return [_to_response(i) for i in repo.list_by_user(user.id)]


@router.post("", response_model=ComplianceItemResponse, status_code=201)
def create_compliance_item(
    payload: ComplianceItemPayload, user: UserDep, repo: RepoDep
) -> ComplianceItemResponse:
    """Create a new compliance item for the caller."""
    _validate(payload)
    now = utc_now()
    item = ComplianceItem(
        id=str(uuid.uuid4()),
        user_id=user.id,
        item_type=payload.item_type,
        label=payload.label,
        due_date=payload.due_date,
        notes=payload.notes,
        completed_at=None,
        created_at=now,
        updated_at=now,
    )
    return _to_response(repo.create(item))


@router.put("/{item_id}", response_model=ComplianceItemResponse)
def update_compliance_item(
    item_id: str, payload: ComplianceItemPayload, user: UserDep, repo: RepoDep
) -> ComplianceItemResponse:
    """Update an existing compliance item (full replace of editable fields)."""
    _validate(payload)
    existing = repo.get(item_id, user.id)
    if existing is None:
        raise NotFoundError("Compliance item not found")
    existing.item_type = payload.item_type
    existing.label = payload.label
    existing.due_date = payload.due_date
    existing.notes = payload.notes
    existing.updated_at = utc_now()
    return _to_response(repo.update(existing))


@router.post("/{item_id}/complete", response_model=ComplianceItemResponse)
def complete_compliance_item(
    item_id: str, user: UserDep, repo: RepoDep
) -> ComplianceItemResponse:
    """Mark an item as completed (e.g. attestation done, training renewed)."""
    existing = repo.get(item_id, user.id)
    if existing is None:
        raise NotFoundError("Compliance item not found")
    now = utc_now()
    existing.completed_at = now
    existing.updated_at = now
    return _to_response(repo.update(existing))


@router.delete("/{item_id}", status_code=204)
def delete_compliance_item(item_id: str, user: UserDep, repo: RepoDep) -> None:
    if not repo.delete(item_id, user.id):
        raise NotFoundError("Compliance item not found")
