# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request / response models for the problem-list API."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import normalize_icd10_code

if TYPE_CHECKING:
    from .models import Problem

ProblemStatusValue = Literal["active", "rule_out", "resolved"]


class AddProblemRequest(BaseModel):
    """Body for ``POST /api/patients/{patient_id}/problems``.

    The same call adds a problem from the chart and from a note: a note passes
    its id as ``source_note_id``. ``diagnostic_assessment_id`` links a
    diagnostic worksheet to the new problem as the evidence for it.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    label: str = Field(min_length=1, max_length=255)
    icd10_code: str | None = None
    status: ProblemStatusValue = "active"
    onset_date: date | None = None
    source_note_id: str | None = None
    diagnostic_assessment_id: str | None = None

    @field_validator("icd10_code")
    @classmethod
    def _code_shape(cls, v: str | None) -> str | None:
        return normalize_icd10_code(v)


class UpdateProblemRequest(BaseModel):
    """Body for ``PATCH /api/patients/{patient_id}/problems/{problem_id}``.

    Omitted fields are left as they are. ``icd10_code`` and ``onset_date``
    may be sent as ``null`` to clear them.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    label: str | None = Field(default=None, min_length=1, max_length=255)
    icd10_code: str | None = None
    status: ProblemStatusValue | None = None
    onset_date: date | None = None

    @field_validator("icd10_code")
    @classmethod
    def _code_shape(cls, v: str | None) -> str | None:
        return normalize_icd10_code(v)


class ReorderProblemsRequest(BaseModel):
    """Every live problem's id, in the order the list should read."""

    problem_ids: list[str] = Field(min_length=1, max_length=200)


class ProblemResponse(BaseModel):
    id: str
    patient_id: str
    label: str
    icd10_code: str | None
    status: ProblemStatusValue
    onset_date: date | None
    position: int
    source_note_id: str | None
    added_by: str | None
    added_at: datetime
    resolved_at: datetime | None
    updated_at: datetime

    @classmethod
    def from_problem(cls, problem: Problem) -> ProblemResponse:
        return cls(
            id=problem.id,
            patient_id=problem.patient_id,
            label=problem.label,
            icd10_code=problem.icd10_code,
            status=cast("ProblemStatusValue", problem.status),
            onset_date=problem.onset_date,
            position=problem.position,
            source_note_id=problem.source_note_id,
            added_by=problem.added_by,
            added_at=problem.added_at,
            resolved_at=problem.resolved_at,
            updated_at=problem.updated_at,
        )


class ProblemListResponse(BaseModel):
    data: list[ProblemResponse]
    total: int
