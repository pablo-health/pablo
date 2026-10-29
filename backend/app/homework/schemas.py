# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request and response shapes for homework, clinician and portal.

Suggestions are chart proposals and use that surface's shapes
(``app.chart_proposals.schemas``); these are the assignments.

The portal shape is an allow-list: the task, when it was given, and the
client's own completion. Never the note it came from, never the wording that
was suggested, never who assigned it.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — pydantic resolves field types at runtime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from .repository import Assignment

MAX_TEXT = 300
MAX_CLIENT_NOTE = 500


def _clean(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("must not be empty")
    return stripped


class HomeworkAssignmentResponse(BaseModel):
    id: str
    #: The note an accepted suggestion came from; ``None`` for one added by hand.
    note_id: str | None
    source_field: str | None
    #: The wording that was suggested, when the clinician changed it.
    proposed_text: str | None
    text: str
    status: Literal["assigned", "done", "withdrawn"]
    origin: Literal["note", "clinician"]
    assigned_at: datetime
    completed_at: datetime | None
    client_note: str | None
    created_at: datetime

    @classmethod
    def from_assignment(cls, a: Assignment) -> HomeworkAssignmentResponse:
        return cls(
            id=a.id,
            note_id=a.note_id,
            source_field=a.source_field,
            proposed_text=a.proposed_text if a.proposed_text != a.text else None,
            text=a.text,
            status=a.status,  # type: ignore[arg-type]  # the repository stores plain strings; the write path validates them against the Literal
            origin=a.origin,  # type: ignore[arg-type]  # the repository stores plain strings; the write path validates them against the Literal
            assigned_at=a.assigned_at,
            completed_at=a.completed_at,
            client_note=a.client_note,
            created_at=a.created_at,
        )


class HomeworkOverviewResponse(BaseModel):
    """A client's homework on the chart."""

    #: Assigned, done and withdrawn, newest first.
    assignments: list[HomeworkAssignmentResponse]


class AddHomeworkRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT)

    @field_validator("text")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return _clean(value)


class EditHomeworkRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT)

    @field_validator("text")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return _clean(value)


# --- portal ---


class PatientHomeworkResponse(BaseModel):
    id: str
    text: str
    status: Literal["assigned", "done"]
    assigned_at: datetime
    completed_at: datetime | None
    client_note: str | None

    @classmethod
    def from_assignment(cls, a: Assignment) -> PatientHomeworkResponse:
        return cls(
            id=a.id,
            text=a.text,
            status=a.status,  # type: ignore[arg-type]  # the repository stores plain strings; the write path validates them against the Literal
            assigned_at=a.assigned_at,
            completed_at=a.completed_at,
            client_note=a.client_note,
        )


class PatientHomeworkList(BaseModel):
    data: list[PatientHomeworkResponse]
    total: int


class MarkHomeworkDoneRequest(BaseModel):
    note: str | None = Field(default=None, max_length=MAX_CLIENT_NOTE)

    @field_validator("note")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None
