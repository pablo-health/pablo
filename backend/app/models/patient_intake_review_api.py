# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request and response shapes for reviewing a form that has been handed in.

The clinician's side of the review cycle: reading a form question by
question, asking for corrections, entering a value for somebody in the
room, and accepting.

Three things about what is here and what deliberately is not.

**The review view carries provenance, not scores.** Every answer comes back
with who put it there and how many earlier answers it replaced, because a
reader who cannot tell "the patient wrote this" from "we wrote this down
for them" is being shown the stronger claim. Instrument totals and bands
are NOT here, for the same reason they are absent from the submission read
beside it: they were scored onto outcome-measure rows when the form
arrived, and the chart already trends and bands those. A second copy would
be the same instrument on screen twice, from two sources free to disagree.

**A request names questions and nothing else.** ``item_ids`` on a
correction request is a list of ids; which patient, which form and who is
asking all come from the path and the principal. There is no field a caller
could set to reopen somebody else's form.

**The note is required.** A correction request with no note leaves the
patient looking at a reopened question with nothing said about why, which
is the one thing the screen exists to tell them.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .patient_intake_assignment_api import (
    ClinicianIntakeAnswerResponse,
    IntakeAssignmentResponse,
)
from .patient_intake_signature_api import (  # noqa: TC001 — pydantic resolves it at runtime
    IntakeSignatureResponse,
)

#: The longest note a correction request carries. Long enough for a
#: paragraph explaining what to redo, short enough that the field is not
#: somewhere a clinical narrative ends up living.
MAX_NOTE_LENGTH = 1000


class RequestCorrectionRequest(BaseModel):
    """``POST …/intake-assignments/{id}/request-correction``."""

    model_config = ConfigDict(extra="forbid")

    #: At least one: reopening no questions would move the form out of the
    #: patient's hands and give them nothing to do with it.
    item_ids: list[str] = Field(min_length=1, max_length=200)
    note: str = Field(min_length=1, max_length=MAX_NOTE_LENGTH)


class ClinicianEntryRequest(BaseModel):
    """``POST …/intake-assignments/{id}/items/{item_id}/clinician-entry``.

    The same open mapping the patient's save takes, and validated by the
    same code against the same question — a value entered in the room is
    held to the answer rules the portal is held to, or the two surfaces
    would disagree about what the form contains.
    """

    model_config = ConfigDict(extra="forbid")

    value: dict[str, object]


class IntakeReviewEventResponse(BaseModel):
    """One thing the practice did with this form.

    ``created_by`` is whoever acted: a clinician's user id on the three the
    practice does, and the patient's own id on the one they cause.
    """

    id: str
    kind: str
    item_ids: list[str]
    note_to_patient: str | None
    created_by: str | None
    created_at: datetime


class IntakeReviewItemResponse(ClinicianIntakeAnswerResponse):
    """One question, what it currently holds, and where that came from.

    ``provenance`` is unset when nobody has answered yet, which is what
    keeps "no answer" distinct from "the patient answered" — the two look
    the same if an unanswered question reports a default.

    ``superseded_count`` is how many earlier answers this question has had.
    A count rather than the answers themselves: the review screen offers to
    show the history, and the history is a read of its own.
    """

    provenance: str | None
    superseded_count: int


class IntakeReviewResponse(IntakeAssignmentResponse):
    """A form as the clinician reviews it.

    Everything the chart needs on one screen: the questions with their
    answers and provenance, what has been signed, and the log of what has
    been asked for and done. Reading it is a disclosure — it is the
    patient's own words — and is audited as one.
    """

    patient_id: str
    items: list[IntakeReviewItemResponse]
    signatures: list[IntakeSignatureResponse]
    events: list[IntakeReviewEventResponse]


__all__ = [
    "MAX_NOTE_LENGTH",
    "ClinicianEntryRequest",
    "IntakeReviewEventResponse",
    "IntakeReviewItemResponse",
    "IntakeReviewResponse",
    "RequestCorrectionRequest",
]
