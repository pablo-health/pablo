# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request and response shapes for intake assignments and saved answers.

Both surfaces are here — the clinician asking somebody to fill a form in,
and the patient filling it in — because they describe the same rows from
two sides and a change to one is nearly always a change to the other.

Two shapes do most of the work.

``IntakeProgressResponse`` is the server's answer to "is this finished".
It carries a boolean and the ids of the questions still outstanding, and
it is the only thing any client is allowed to believe about completion —
which is why it is a response model and has no request counterpart.

``SaveAnswerRequest`` carries a value and nothing else. The patient, the
assignment and the question are all in the path or the principal, so there
is no field a caller could set to answer on somebody else's behalf. The
value stays an open mapping for the reason the item's ``config`` does:
what a valid answer looks like depends on the question, and
:mod:`app.intake.answers` is where that is decided.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CreateAssignmentRequest(BaseModel):
    """``POST /api/patients/{patient_id}/intake-assignments``."""

    model_config = ConfigDict(extra="forbid")

    version_id: str = Field(min_length=1, max_length=64)


class SaveAnswerRequest(BaseModel):
    """``PUT /api/patient/intake/assignments/{id}/items/{item_id}``."""

    model_config = ConfigDict(extra="forbid")

    value: dict[str, object]


class IntakeProgressResponse(BaseModel):
    """How much of a form is still outstanding.

    ``missing`` holds item ids in the order the form asks them, so a client
    can send somebody to the first one without sorting anything.
    """

    complete: bool
    missing: list[str]


class IntakeAssignmentResponse(BaseModel):
    """One form somebody was asked to fill in.

    ``packet_name`` is carried rather than looked up separately, because a
    list of assignments with no names on it is a list of identifiers. The
    version number goes with it so "Intake, version 3" reads the way the
    settings editor writes it.
    """

    id: str
    version_id: str
    packet_name: str
    version: int
    status: str
    assigned_at: datetime
    submitted_at: datetime | None
    progress: IntakeProgressResponse


class IntakeAssignmentItemResponse(BaseModel):
    """One question as the person answering it sees it.

    ``value`` is whatever they have saved so far, absent when they have not
    answered yet. ``position`` rides along so a client can render the form
    in order without trusting the order of a JSON array.
    """

    id: str
    key: str
    position: int
    item_type: str
    required: bool
    config: dict[str, object]
    value: dict[str, object] | None


class IntakeAssignmentDetailResponse(IntakeAssignmentResponse):
    """One assignment, its questions, and the answers saved against them."""

    items: list[IntakeAssignmentItemResponse]


class SavedAnswerResponse(BaseModel):
    """One saved answer, and what saving it did to the form as a whole.

    The progress comes back with the save so a client never has to ask a
    second time to know whether the form can be handed in — and never has
    to work it out for itself, which is the point.
    """

    item_id: str
    saved_at: datetime
    status: str
    progress: IntakeProgressResponse


__all__ = [
    "CreateAssignmentRequest",
    "IntakeAssignmentDetailResponse",
    "IntakeAssignmentItemResponse",
    "IntakeAssignmentResponse",
    "IntakeProgressResponse",
    "SaveAnswerRequest",
    "SavedAnswerResponse",
]
