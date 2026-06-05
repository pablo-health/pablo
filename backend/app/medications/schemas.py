# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Pydantic request / response models for the medications API."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class CreateMedicationRequest(BaseModel):
    """Body for ``POST /api/patients/{patient_id}/medications``."""

    model_config = ConfigDict(str_strip_whitespace=True)

    drug_name: str
    dose: str
    status: str = "active"
    started_at: date | None = None
    notes: str | None = None


class UpdateMedicationRequest(BaseModel):
    """Body for ``PATCH /api/patients/{patient_id}/medications/{medication_id}``.

    All fields are optional to support partial-update (patch) semantics.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    drug_name: str | None = None
    dose: str | None = None
    status: str | None = None
    started_at: date | None = None
    stopped_at: date | None = None
    notes: str | None = None


class MedicationResponse(BaseModel):
    """Single medication row as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    patient_id: str
    drug_name: str
    dose: str
    status: str
    started_at: date | None
    stopped_at: date | None
    notes: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime


class MedicationListResponse(BaseModel):
    """List of medication rows."""

    data: list[MedicationResponse]
    total: int
