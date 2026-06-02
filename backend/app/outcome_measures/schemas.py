# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Pydantic request / response models for the outcome measures API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from ..models.enums import OutcomeMeasureSource


class CreateOutcomeMeasureRequest(BaseModel):
    """Body for ``POST /api/patients/{patient_id}/outcome-measures``."""

    model_config = ConfigDict(str_strip_whitespace=True)

    instrument: str
    """Short instrument code, e.g. ``'phq9'`` or ``'gad7'``."""

    source: OutcomeMeasureSource
    """Clinical provenance of the score — required, no default."""

    administered_at: datetime
    """When the instrument was administered (timezone-aware)."""

    session_id: str | None = None
    """Optional link to a recorded session."""

    appointment_id: str | None = None
    """Optional link to a scheduled appointment."""

    item_scores: dict[str, int] | None = None
    """Per-item responses.  When supplied, total_score and is_complete are
    derived by the service layer via the instrument registry."""

    total_score: int | None = None
    """Explicit summary total when item_scores are not available.  At least
    one of *item_scores* or *total_score* must be provided."""


class OutcomeMeasureResponse(BaseModel):
    """Single outcome measure row as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    patient_id: str
    session_id: str | None
    appointment_id: str | None
    instrument: str
    total_score: int | None
    item_scores: dict[str, int] | None
    is_complete: bool
    source: str
    item_citations: dict[str, object] | None
    administered_at: datetime
    created_by: str
    created_at: datetime
    updated_at: datetime
    # Computed at read time; not stored in the DB.
    severity: str | None


class OutcomeMeasureListResponse(BaseModel):
    """Paginated list of outcome measure rows."""

    data: list[OutcomeMeasureResponse]
    total: int
