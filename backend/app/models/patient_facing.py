# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a patient may read of their own chart and their own calendar.

Row-level security decides which ROWS a patient reaches. It has nothing to say
about columns. A patient principal holds a policy on their own ``patients`` row
and on their own ``appointments`` rows, so at the database layer every column of
both is readable by them — the clinician's working diagnosis, the note about
what they can afford to pay, the visit's billing codes, the calendar-sync
internals. The database will not catch a widening here, because as far as the
policy is concerned nothing was widened.

So the control is in this module, and it has three parts.

* **One response model per table, not per route.** Every patient-facing route
  that returns a chart row returns :class:`PatientFacingPatient`; every one that
  returns an appointment returns :class:`PatientAppointmentResponse`. A second
  route wanting a second shape is the moment to notice, not a thing to add.
* **A decision recorded for every column.** The two registries below name every
  column of their table and say either "the model carries it" or, in one line,
  why it is withheld. A column added to the ORM and not to its registry fails
  ``backend/tests/test_patient_facing_columns.py``, so a future column is
  invisible by default rather than exposed by default.
* **The repositories project.** The reads behind these models select the named
  columns rather than the row, so a widening would have to happen twice.

The framing, because it comes up on every field: right of access under the
Privacy Rule covers the designated record set, so a patient may well be entitled
to their diagnosis or their visit coding ON REQUEST. A self-serve API response
is not that request, and minimum necessary governs what a route returns by
default.
"""

from __future__ import annotations

# Runtime import: Pydantic resolves this annotation at runtime for validation,
# so it cannot live in a TYPE_CHECKING block.
from datetime import datetime
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..scheduling_engine.models.appointment import Appointment

#: The decision value meaning "the patient-facing model for this table carries
#: this column". Every other value in a registry is the one-line reason the
#: column is withheld, which is why the shown case is the absence of a reason.
SHOWN: Final[None] = None


def shown_columns(decisions: Mapping[str, str | None]) -> frozenset[str]:
    """The column names a registry says a patient may see."""
    return frozenset(name for name, reason in decisions.items() if reason is None)


def withheld_columns(decisions: Mapping[str, str | None]) -> dict[str, str]:
    """The withheld column names, each mapped to why."""
    return {name: reason for name, reason in decisions.items() if reason is not None}


class PatientFacingPatient(BaseModel):
    """A chart row, as its own subject may see it.

    Deliberately NOT ``Patient``, the domain model the clinician surfaces use.
    That one carries the whole row, and the whole row is readable by this
    principal at the database layer — including ``sliding_scale_note``, which is
    a staff note about a person's ability to pay, and would otherwise be
    surfaced to that person without anyone deciding to.

    Three fields, because three fields are what a patient-facing route has
    needed so far: the intake form shows a patient the name and date of birth
    the chart holds and asks whether they are right. A fourth field belongs here
    the day a route needs it, added on purpose.
    """

    first_name: str
    last_name: str
    date_of_birth: str | None = None


class PatientAppointmentResponse(BaseModel):
    """One appointment, as its own patient may see it.

    Deliberately NOT ``AppointmentResponse``. The appointments table is readable
    by its patient in full at the database layer — including ``diagnosis_codes``,
    ``service_code`` and the other visit-coding fields, the clinician's
    ``notes``, the owning clinician's ``user_id``, ``session_id``,
    ``confirmation_token_hash`` and the calendar-sync internals. This model is
    the only thing between a patient and all of that, which is why it names its
    fields rather than inheriting them.

    What is withheld, and why, is recorded column by column in
    :data:`APPOINTMENT_COLUMN_DECISIONS` below rather than in prose here, so the
    list cannot drift from the table it describes.

    ``recurrence_rule`` IS included: "every week" is something the person
    attending should be able to see about their own appointment.
    """

    id: str
    start_at: datetime
    end_at: datetime
    duration_minutes: int
    status: str
    session_type: str
    video_link: str | None = None
    video_platform: str | None = None
    recurrence_rule: str | None = None
    recurring_appointment_id: str | None = None
    late_cancellation: bool | None = Field(
        default=None,
        description=(
            "True when this was cancelled with less notice than the practice "
            "asks for, so its notice policy may apply. None on anything not "
            "cancelled, and on cancellations made before this was recorded."
        ),
    )
    """Whether a fee may follow, told to the person who might be charged it.

    Included where the rest of the billing fields are deliberately withheld,
    because this one is about the patient's own conduct and its consequence for
    them — not staff-authored coding. Being charged a late-cancellation fee
    without ever being told the cancellation counted as late is exactly the
    surprise the withholding rule exists to prevent. The fee AMOUNT stays out:
    that is the practice's rate card, and it lives on the patient's chart rather
    than on the appointment.
    """

    @classmethod
    def from_appointment(cls, appointment: Appointment) -> PatientAppointmentResponse:
        """Project a domain appointment down to what its patient may see.

        Field by field rather than ``model_validate``, so a future field on the
        domain model never becomes a response field by default. One projector
        rather than one per route: the booking routes read the whole row because
        the scheduling engine needs it, and they must arrive at the same answer
        as the list route, which reads only these columns.
        """
        return cls(
            id=appointment.id,
            start_at=appointment.start_at,
            end_at=appointment.end_at,
            duration_minutes=appointment.duration_minutes,
            status=appointment.status,
            session_type=appointment.session_type,
            video_link=appointment.video_link,
            video_platform=appointment.video_platform,
            recurrence_rule=appointment.recurrence_rule,
            recurring_appointment_id=appointment.recurring_appointment_id,
            late_cancellation=appointment.late_cancellation,
        )


#: Every column of ``patients``, and what a patient-facing route does with it.
PATIENT_COLUMN_DECISIONS: Final[Mapping[str, str | None]] = {
    "id": "The caller's own id, which the principal already established.",
    "first_name": SHOWN,
    "last_name": SHOWN,
    "first_name_lower": "A search-index copy of the name above.",
    "last_name_lower": "A search-index copy of the name above.",
    "email": "Contact details as staff recorded them; no patient-facing screen reads them yet.",
    "phone": "Contact details as staff recorded them; no patient-facing screen reads them yet.",
    "status": "Chart workflow state, an operational label rather than a fact about the person.",
    "date_of_birth": SHOWN,
    "diagnosis": "The clinician's working impression, written for the record and not as news.",
    "session_count": "Caseload bookkeeping.",
    "last_session_date": "A denormalised scheduling summary; the calendar routes answer this.",
    "next_session_date": "A denormalised scheduling summary; the calendar routes answer this.",
    "created_at": "When the chart row was written — record bookkeeping.",
    "updated_at": "When the chart row was last edited — record bookkeeping.",
    "deleted_at": "A soft-delete marker; a read that reaches this row has already filtered on it.",
    "chart_closed_at": "Whether the care episode ended — news that should come from a person.",
    "chart_closure_reason": "Staff-authored, and a reason that should come from a person.",
    "phi_email_consent": "The consent decision as staff recorded it, not the patient's copy of it.",
    "phi_email_consent_at": "When that decision was recorded.",
    "phi_email_consent_doc": "The document backing the attestation.",
    "phi_email_consent_by": "Which staff member attested to it.",
    "rate_cents": "What this person is charged is a billing conversation, not a chart field.",
    "sliding_scale_note": "A staff note about this person's ability to pay.",
    "origin": "Flags a row created through an unverified intake surface, for a human to review.",
    "address_line1": "The mailing address as staff recorded it for claims.",
    "address_line2": "The mailing address as staff recorded it for claims.",
    "city": "The mailing address as staff recorded it for claims.",
    "state": "The mailing address as staff recorded it for claims.",
    "postal_code": "The mailing address as staff recorded it for claims.",
    "sex": "The administrative sex code a claim's demographic segment expects.",
}

#: Every column of ``appointments``, and what a patient-facing route does with
#: it. Two reasons cover most of the table: staff-authored billing or clinical
#: coding, and internal identifiers or sync state.
APPOINTMENT_COLUMN_DECISIONS: Final[Mapping[str, str | None]] = {
    "id": SHOWN,
    "user_id": "The clinician's internal identifier; the patient knows who they see.",
    "patient_id": "The caller's own id, which the principal already established.",
    "title": "Free text a clinician writes on the slot; `session_type` is what was booked.",
    "start_at": SHOWN,
    "end_at": SHOWN,
    "duration_minutes": SHOWN,
    "status": SHOWN,
    "pending_expires_at": "When a held slot lapses; nothing patient-facing explains it yet.",
    "confirmation_token_hash": "A credential digest.",
    "cancelled_at": "Cancellation bookkeeping behind `status`.",
    "cancelled_by": "Who cancelled, recorded so a fee is defensible afterwards.",
    "cancelled_by_id": "An internal identifier for whoever cancelled.",
    "late_cancellation": SHOWN,
    "superseded_by_id": "An internal link to the replacement row after a reschedule.",
    "late_change_acknowledged": "The attestation the API already required from the caller.",
    "appointment_type_id": "Internal scheduling bookkeeping with no meaning to a patient.",
    "session_type": SHOWN,
    "video_link": SHOWN,
    "video_platform": SHOWN,
    "notes": "Written by the clinician, for the clinician.",
    "note_type": "An internal link to the clinical record this visit produces.",
    "recurrence_rule": SHOWN,
    "recurring_appointment_id": SHOWN,
    "recurrence_index": "Internal scheduling bookkeeping with no meaning to a patient.",
    "is_exception": "Internal scheduling bookkeeping with no meaning to a patient.",
    "google_event_id": "Calendar-sync state.",
    "google_calendar_id": "Calendar-sync state.",
    "google_sync_status": "Calendar-sync state.",
    "ical_uid": "Calendar-sync state.",
    "ical_source": "Calendar-sync state.",
    "ical_sync_status": "Calendar-sync state.",
    "ehr_appointment_url": "An internal system URL.",
    "session_id": "An internal link to the therapy-session record.",
    "service_code": "Billing and clinical coding, staff-authored.",
    "modifiers": "Billing and clinical coding, staff-authored.",
    "unit_count": "Billing and clinical coding, staff-authored.",
    "place_of_service": "Billing and clinical coding, staff-authored.",
    "diagnosis_codes": "Billing and clinical coding, staff-authored.",
    "reminder_24h_sent": "Whether a reminder job has run — delivery bookkeeping.",
    "reminder_1h_sent": "Whether a reminder job has run — delivery bookkeeping.",
    "created_at": "When the row was written, which is not when the appointment is.",
    "updated_at": "When the row was last edited, which is not when the appointment is.",
}


__all__ = [
    "APPOINTMENT_COLUMN_DECISIONS",
    "PATIENT_COLUMN_DECISIONS",
    "SHOWN",
    "PatientAppointmentResponse",
    "PatientFacingPatient",
    "shown_columns",
    "withheld_columns",
]
