# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request and response shapes for refill requests.

The patient and the practice see different shapes of the same row, and the
difference is the point: the practice's note on a decision is theirs, so
nothing the patient surface returns has a field for it.

Nothing here accepts a ``patient_id`` or a ``status`` from a patient. The
patient comes from the calling principal, and every request starts as
``requested``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, model_validator

from .refill_request import RefillRisk, RunOutSource

if TYPE_CHECKING:
    from .refill_request import RefillQueueEntry, RefillRequest

# What a prescriber may answer with. ``requested`` is where every request
# starts and is never a decision.
RefillDecision = Literal["approved", "needs_visit", "declined"]

MAX_MEDICATION_TEXT = 200
MAX_PHARMACY_TEXT = 200
MAX_PATIENT_NOTE = 2_000
MAX_PRESCRIBER_NOTE = 2_000
#: The most days a client may say they have left; a longer supply needs no timing.
MAX_DAYS_LEFT = 365


class CreateRefillRequest(BaseModel):
    """``POST /api/patient/refills`` body.

    Exactly one of ``medication_id`` and ``medication_text``: a patient either
    picks from their own list or types a name, and a request carrying both
    would leave the practice guessing which one was meant.

    ``days_left`` is the client's answer to how many days of the medication
    they have left, 0 meaning they run out today. Optional: a client who does
    not know leaves it out.
    """

    medication_id: str | None = None
    medication_text: str | None = Field(default=None, min_length=1, max_length=MAX_MEDICATION_TEXT)
    pharmacy_text: str | None = Field(default=None, max_length=MAX_PHARMACY_TEXT)
    patient_note: str | None = Field(default=None, max_length=MAX_PATIENT_NOTE)
    days_left: int | None = Field(default=None, ge=0, le=MAX_DAYS_LEFT)

    @model_validator(mode="after")
    def _one_medication(self) -> CreateRefillRequest:
        if (self.medication_id is None) == (self.medication_text is None):
            raise ValueError("Choose a medication or type its name.")
        return self


class DecideRefillRequest(BaseModel):
    """``POST /api/refill-requests/{id}/decision`` body."""

    status: RefillDecision
    prescriber_note: str | None = Field(default=None, max_length=MAX_PRESCRIBER_NOTE)


class PatientMedicationOption(BaseModel):
    """One medication a patient may ask to have refilled.

    Two descriptive fields and the id, pinned: the chart row carries the
    practice's notes and stop reasons, and none of that is the patient's to
    read from here.
    """

    id: str
    drug_name: str
    dose: str


class PatientMedicationOptionList(BaseModel):
    data: list[PatientMedicationOption]
    total: int


class PatientRefillRequestResponse(BaseModel):
    """A request as the patient who made it sees it."""

    id: str
    medication_id: str | None = None
    medication_text: str
    pharmacy_text: str | None = None
    patient_note: str | None = None
    status: str
    created_at: datetime
    decided_at: datetime | None = None

    @staticmethod
    def from_request(request: RefillRequest) -> PatientRefillRequestResponse:
        return PatientRefillRequestResponse(
            id=request.id,
            medication_id=request.medication_id,
            medication_text=request.medication_text,
            pharmacy_text=request.pharmacy_text,
            patient_note=request.patient_note,
            status=request.status,
            created_at=request.created_at,
            decided_at=request.decided_at,
        )


class PatientRefillRequestList(BaseModel):
    data: list[PatientRefillRequestResponse]
    total: int


class RefillRequestResponse(BaseModel):
    """A request as the practice sees it.

    The last four fields are the practice's read of time and risk (see
    :mod:`app.models.refill_risk`): when the client runs out and how that
    is known, whether the medication is a controlled substance (Schedule
    II-V), and whether it is harmful to interrupt. Set on the queue and on a
    single read; a decision's response carries the stored run-out date only.
    """

    id: str
    patient_id: str
    patient_name: str | None = None
    medication_id: str | None = None
    medication_text: str
    pharmacy_text: str | None = None
    patient_note: str | None = None
    status: str
    created_at: datetime
    decided_at: datetime | None = None
    decided_by_user_id: str | None = None
    prescriber_note: str | None = None
    #: The practice's own target for answering; never on the patient's view.
    due_at: datetime | None = None
    run_out_date: date | None = None
    run_out_source: RunOutSource | None = None
    controlled: bool = False
    interruption_risk: bool = False

    @staticmethod
    def from_request(
        request: RefillRequest, patient_name: str | None = None, risk: RefillRisk | None = None
    ) -> RefillRequestResponse:
        if risk is None:
            risk = RefillRisk(
                run_out_date=request.run_out_date,
                run_out_source="client" if request.run_out_date is not None else None,
            )
        return RefillRequestResponse(
            id=request.id,
            patient_id=request.patient_id,
            patient_name=patient_name,
            medication_id=request.medication_id,
            medication_text=request.medication_text,
            pharmacy_text=request.pharmacy_text,
            patient_note=request.patient_note,
            status=request.status,
            created_at=request.created_at,
            decided_at=request.decided_at,
            decided_by_user_id=request.decided_by_user_id,
            prescriber_note=request.prescriber_note,
            due_at=request.due_at,
            run_out_date=risk.run_out_date,
            run_out_source=risk.run_out_source,
            controlled=risk.controlled,
            interruption_risk=risk.interruption_risk,
        )

    @staticmethod
    def from_queue_entry(entry: RefillQueueEntry) -> RefillRequestResponse:
        first = entry.patient_preferred_name or entry.patient_first_name
        return RefillRequestResponse.from_request(
            entry.request, patient_name=f"{first} {entry.patient_last_name}", risk=entry.risk
        )


class RefillRequestList(BaseModel):
    data: list[RefillRequestResponse]
    total: int
