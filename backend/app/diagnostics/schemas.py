# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Pydantic request / response models for the diagnostics API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from ..models.enums import OutcomeMeasureSource


class CreateDiagnosticAssessmentRequest(BaseModel):
    """Body for ``POST /api/patients/{patient_id}/diagnostic-assessments``."""

    model_config = ConfigDict(str_strip_whitespace=True)

    instrument: str
    """Definition code (matches a row in ``diagnostic_definitions``)."""

    source: OutcomeMeasureSource
    """Clinical provenance — ``'manual'`` for clinician entry."""

    assessed_at: datetime
    criterion_responses: dict[str, bool]
    gate_responses: dict[str, bool]

    session_id: str | None = None
    appointment_id: str | None = None
    # Clinician-confirmed ICD-10-CM code. Must be one of the definition's
    # options when supplied. Omitted for a determination that doesn't meet
    # criteria.
    determined_icd10: str | None = None
    diagnosis_label: str | None = None


# Re-exported building blocks so the form can render a definition.
class CriterionView(BaseModel):
    key: str
    label: str
    cardinal: bool


class CriterionGroupView(BaseModel):
    key: str
    label: str
    min_met: int
    require_cardinal: bool
    criteria: list[CriterionView]


class GateView(BaseModel):
    key: str
    label: str


class ICD10OptionView(BaseModel):
    code: str
    label: str


class DiagnosticDefinitionResponse(BaseModel):
    """A definition, shaped for rendering the criterion form."""

    code: str
    version: int
    display_name: str
    evaluator_type: str
    criterion_groups: list[CriterionGroupView]
    gates: list[GateView]
    icd10_options: list[ICD10OptionView]
    suggested_icd10: str | None


class DiagnosticDefinitionListResponse(BaseModel):
    data: list[DiagnosticDefinitionResponse]
    total: int


# --- Prescribing-support views (optional reference data on a definition) ---


class DifferentialCueView(BaseModel):
    cue_text: str
    citation: str | None


class DifferentialView(BaseModel):
    icd_code: str
    mimics_how: str | None
    distinguish_how: str | None
    transcript_cues: list[DifferentialCueView]


class PrescribingSafeguardView(BaseModel):
    key: str
    label: str
    applies_when: str | None
    citation: str | None


class MedicationRationaleView(BaseModel):
    first_line: list[str]
    alternatives: list[str]
    stepped_care: str | None
    this_agent_now: str | None
    citations: list[str]


class PrescribingSupportResponse(BaseModel):
    """A definition's optional prescribing-support reference data.

    Decision-support reference material only — never a verdict on whether a
    differential is present or which agent to choose. Empty lists / ``None``
    when a definition carries no such data (the default).
    """

    code: str
    version: int
    display_name: str
    differentials: list[DifferentialView]
    prescribing_safeguards: list[PrescribingSafeguardView]
    medication_rationale: MedicationRationaleView | None


class DiagnosticAssessmentResponse(BaseModel):
    """A recorded diagnostic determination."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    patient_id: str
    session_id: str | None
    appointment_id: str | None
    instrument: str
    definition_version: int
    criterion_responses: dict[str, bool]
    gate_responses: dict[str, bool]
    meets_criteria: bool | None
    determined_icd10: str | None
    diagnosis_label: str | None
    source: str
    confirmed_at: datetime | None
    assessed_at: datetime
    created_by: str
    created_at: datetime
    updated_at: datetime
    # Computed at read time from the definition; not stored.
    suggested_icd10: str | None
    unmet_reasons: list[str]


class DiagnosticAssessmentListResponse(BaseModel):
    data: list[DiagnosticAssessmentResponse]
    total: int
