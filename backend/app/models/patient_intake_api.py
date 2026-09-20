# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request and response shapes for the patient-principal intake surface.

Two rules shape every model here, and both come from who is on the other
end of the wire.

**No patient id, anywhere.** Not in the request, not in the response. The
id comes off :class:`~app.auth.patient_context.PatientContext`, so there is
nothing for a caller to substitute and nothing for a handler to remember to
compare.

**The form is the server's, not the client's.** The GET response carries the
item wording and the response anchors, so the thing rendering the form has no
copy of its own to drift. A submission names items by key and value only.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class IntakeResponseOptionResponse(BaseModel):
    """One answer choice, and the value it scores."""

    value: int
    label: str


class IntakeInstrumentResponse(BaseModel):
    """A screener as the form renders it.

    ``items`` is keyed by the same item keys a submission sends back, so a
    renderer never has to derive "1".."9" itself.
    """

    code: str
    display_name: str
    prompt: str
    items: dict[str, str]
    response_options: list[IntakeResponseOptionResponse]


class IntakeIdentityResponse(BaseModel):
    """What the chart currently says about the person filling this in.

    Shown so they can confirm it or say what is wrong. The form never
    changes the chart.
    """

    first_name: str
    last_name: str
    date_of_birth: str | None = None


class IntakeFormResponse(BaseModel):
    """``GET /api/patient/intake/form``."""

    identity: IntakeIdentityResponse
    reason_prompt: str
    instruments: list[IntakeInstrumentResponse]


class SubmitIntakeRequest(BaseModel):
    """``POST /api/patient/intake/submissions`` body.

    ``name_confirmed`` and ``dob_confirmed`` are attestations, not
    adjudications: a "no" is recorded for the clinician to read alongside
    ``corrections``, and never blocks the submission or edits the chart.

    Every item of both screeners is required. A partial screener would score
    against bands built for a complete one, and the handler rejects it rather
    than record a total that reads as comparable and is not.
    """

    name_confirmed: bool
    dob_confirmed: bool
    corrections: str | None = Field(default=None, max_length=2_000)
    reason_text: str = Field(min_length=1, max_length=4_000)
    phq9: dict[str, int]
    gad7: dict[str, int]


class IntakeMeasureResponse(BaseModel):
    """One scored screener, as recorded."""

    id: str
    instrument: str
    # Always present for a complete screener, which is the only kind this
    # surface records. Nullable because the column is.
    total_score: int | None = None
    severity: str | None = None


class IntakeSubmissionResponse(BaseModel):
    """``POST /api/patient/intake/submissions`` — 201.

    Echoes what was recorded so the form can confirm it landed. The scores
    come back because the person who answered the questions is the person
    reading the response; nothing here is another patient's, and nothing
    here is the clinician's interpretation of it.
    """

    id: str
    submitted_at: datetime
    measures: list[IntakeMeasureResponse]


__all__ = [
    "IntakeFormResponse",
    "IntakeIdentityResponse",
    "IntakeInstrumentResponse",
    "IntakeMeasureResponse",
    "IntakeResponseOptionResponse",
    "IntakeSubmissionResponse",
    "SubmitIntakeRequest",
]
