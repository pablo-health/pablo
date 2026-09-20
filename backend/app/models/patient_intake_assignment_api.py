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


class IntakeArtifactResponse(BaseModel):
    """One file attached to one question.

    Carries the document's id rather than its contents or its name: the
    portal already has a route that hands back a short-lived URL for a
    document this patient owns, and the name is on that response.
    """

    id: str
    assignment_id: str
    item_id: str
    document_id: str
    side: str | None
    created_at: datetime


class IntakeCorrectionResponse(BaseModel):
    """What the practice has asked this patient to go back and redo.

    Present only while a form is reopened. ``item_ids`` is every question
    the request named, in the order the form asks them, and ``outstanding``
    is the subset not answered again yet — computed from the rows, like
    every other statement about progress on this surface, so a client never
    works out for itself whether the form can go back.

    ``note`` is the practice's own sentence, shown to the patient. It is
    the only free text on this shape and it never carries an answer.
    """

    requested_at: datetime
    note: str | None
    item_ids: list[str]
    outstanding: list[str]


class IntakeAssignmentDetailResponse(IntakeAssignmentResponse):
    """One assignment, its questions, and the answers saved against them.

    ``artifacts`` rides along rather than living on a route of its own, so
    a form that asks for two photographs of a card comes back in one read
    knowing which of them have arrived. The item's own ``value`` names the
    same documents; these rows carry the side and the order they came in.
    """

    items: list[IntakeAssignmentItemResponse]
    artifacts: list[IntakeArtifactResponse] = Field(default_factory=list)
    #: Absent unless the form is open for corrections, which is what makes
    #: its presence the whole answer to "am I being asked to redo
    #: something".
    correction: IntakeCorrectionResponse | None = None


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
    #: What the patient sent in against the questions that asked for files.
    #: Part of the same disclosure the answers are, and read through the
    #: same grant.
    artifacts: list[IntakeArtifactResponse] = Field(default_factory=list)


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


class AttachArtifactRequest(BaseModel):
    """``POST /api/patient/intake/assignments/{id}/artifacts``.

    Which question the file answers, which file, and — on a card — which
    side of it. No patient id and no filename: the first comes off the
    principal, and the second is already on the document row.
    """

    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1, max_length=64)
    document_id: str = Field(min_length=1, max_length=64)
    #: ``"front"`` or ``"back"`` on an insurance card, absent on anything
    #: else. Checked against what the question actually asked for rather
    #: than accepted as given.
    side: str | None = Field(default=None, max_length=8)


class ArtifactWriteResponse(BaseModel):
    """An artifact that was attached or removed, and where the form now is.

    The progress rides along for the same reason it rides on a save: no
    client works out for itself whether a form can be handed in.
    """

    artifact: IntakeArtifactResponse
    status: str
    progress: IntakeProgressResponse


class SaveIntakeCoverageResponse(BaseModel):
    """What typing the plan off a card did.

    ``eligibility_requested`` says whether a check was queued with the
    payer. It is deliberately not a claim about the answer: the check runs
    off this request, the verdict lands on the coverage record minutes
    later or not at all, and nothing the patient sees waits for it.
    """

    coverage_id: str
    eligibility_requested: bool


__all__ = [
    "ArtifactWriteResponse",
    "AttachArtifactRequest",
    "ClinicianIntakeAnswerResponse",
    "ClinicianIntakeAssignmentDetailResponse",
    "CreateAssignmentRequest",
    "IntakeArtifactResponse",
    "IntakeAssignmentDetailResponse",
    "IntakeAssignmentItemResponse",
    "IntakeAssignmentResponse",
    "IntakeCorrectionResponse",
    "IntakeProgressResponse",
    "IntakeSubmissionResponse",
    "SaveAnswerRequest",
    "SaveIntakeCoverageResponse",
    "SavedAnswerResponse",
    "SubmittedMeasureResponse",
]
