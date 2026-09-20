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
    #: The code the patient was given when they handed it in, absent until
    #: then. Not a credential: it unlocks nothing and exists so a person on
    #: the phone and a person at the chart can name the same submission.
    receipt_code: str | None = None
    progress: IntakeProgressResponse


class IntakeAssignmentItemResponse(BaseModel):
    """One question as the person answering it sees it.

    ``value`` is whatever they have saved so far, absent when they have not
    answered yet. ``position`` rides along so a client can render the form
    in order without trusting the order of a JSON array.

    ``label`` is the question itself and ``help_text`` the line under it,
    both as the practice wrote them. Unset on the questions the engine words
    for itself, which is why the portal asks the form route for those rather
    than inventing wording of its own.
    """

    id: str
    key: str
    position: int
    item_type: str
    required: bool
    label: str | None
    help_text: str | None
    config: dict[str, object]
    value: dict[str, object] | None


class IntakeAssignmentDetailResponse(IntakeAssignmentResponse):
    """One assignment, its questions, and the answers saved against them."""

    items: list[IntakeAssignmentItemResponse]


class SubmittedMeasureResponse(BaseModel):
    """One measure the submission scored, as the patient's receipt names it.

    The total and the band are here because the patient answered the
    questions that produced them and the chart shows them the same numbers.
    Nothing interprets either: both come straight from the instrument
    registry, which sums validated items and looks up a published band.
    """

    id: str
    instrument: str
    total_score: int | None
    severity: str | None


class IntakeSubmissionResponse(BaseModel):
    """What handing a form in gives back.

    ``receipt_code`` is the part a person writes down. The rest is what the
    portal needs to stop showing the form as outstanding without asking
    again.

    ``notes`` is what the receipt screen has to say beyond the code, and is
    empty on almost every submission. It carries sentences rather than
    codes because there is one reader and the server is the only thing that
    knows what happened — see
    :func:`app.intake.receipts.withheld_answers_note`.
    """

    assignment_id: str
    version_id: str
    submitted_at: datetime
    receipt_code: str
    measures: list[SubmittedMeasureResponse]
    notes: list[str] = Field(default_factory=list)


class ClinicianIntakeAnswerResponse(BaseModel):
    """One question and what this patient answered, read from the chart.

    The question comes back with the answer for the same reason the patient
    saw it: a value beside a key is a row nobody can read.
    """

    id: str
    key: str
    position: int
    item_type: str
    required: bool
    label: str | None
    help_text: str | None
    config: dict[str, object]
    value: dict[str, object] | None


class ClinicianIntakeAssignmentDetailResponse(IntakeAssignmentResponse):
    """One assignment and the answers on it, for the clinician's chart.

    The same rows the patient sees, from the other side of the room, which
    is why reading this is a disclosure and reading the patient's own copy
    is not.
    """

    patient_id: str
    items: list[ClinicianIntakeAnswerResponse]


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
    "ClinicianIntakeAnswerResponse",
    "ClinicianIntakeAssignmentDetailResponse",
    "CreateAssignmentRequest",
    "IntakeAssignmentDetailResponse",
    "IntakeAssignmentItemResponse",
    "IntakeAssignmentResponse",
    "IntakeProgressResponse",
    "IntakeSubmissionResponse",
    "SaveAnswerRequest",
    "SavedAnswerResponse",
    "SubmittedMeasureResponse",
]
