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

    One model for the table rather than one per route, which is why the
    profile screen widened THIS rather than adding a second shape beside it.
    Two routes read it now and they read different parts: the intake form
    shows a patient the name and date of birth the chart holds and asks
    whether they are right, and the portal profile shows them everything the
    chart has that is theirs to keep current. A field belongs here the day a
    route needs it, added on purpose.

    Identity and contact are both here, and the difference between them is
    enforced a layer up rather than by two models: ``app.routes.patient_profile``
    accepts writes to the contact fields and to ``preferred_name`` only, and
    refuses a name or a date of birth outright.
    """

    first_name: str
    last_name: str
    preferred_name: str | None = None
    date_of_birth: str | None = None
    email: str | None = None
    phone: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None


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
    provider: str | None = Field(
        default=None,
        description=(
            "Which video service this appointment is held on, when it is held "
            "on one. Shown so the portal can say a link is coming before the "
            "link is worth offering."
        ),
    )
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
            provider=appointment.provider,
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
    "preferred_name": SHOWN,
    # Contact details are the patient's own and the portal profile is where
    # they keep them current. Shown because a screen that asks someone to
    # check their address has to show them the address.
    "email": SHOWN,
    "phone": SHOWN,
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
    # The mailing address a claim carries. Shown for the same reason as the
    # contact fields above: it is the patient's own address, and they are the
    # one who knows when it changed.
    "address_line1": SHOWN,
    "address_line2": SHOWN,
    "city": SHOWN,
    "state": SHOWN,
    "postal_code": SHOWN,
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
    "provider": SHOWN,
    "meeting_external_id": "The service's own handle for the room; it opens nothing by itself.",
    # What the vendor said about the call. Withheld for the same reason the
    # reminder flags are: it is delivery bookkeeping about a system, and a
    # patient reading "we recorded you as checked in at 14:58" would be
    # reading an attendance claim nobody has reviewed.
    "telehealth_checked_in_at": "Vendor call bookkeeping, unreviewed.",
    "telehealth_started_at": "Vendor call bookkeeping, unreviewed.",
    "telehealth_ended_at": "Vendor call bookkeeping, unreviewed.",
    "notes": "Written by the clinician, for the clinician.",
    "note_type": "An internal link to the clinical record this visit produces.",
    "note_inputs": "Context the clinician supplied for drafting the visit's note.",
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


#: Of the shown columns, the ones a patient may also CHANGE about themselves.
#:
#: A separate frozenset rather than a third decision value, so widening what a
#: patient can SEE never silently widens what they can WRITE. Everything here
#: is contact information or what they would like to be called: facts the
#: person is the authority on, and that a practice currently learns by being
#: told. The identity fields — ``first_name``, ``last_name``,
#: ``date_of_birth`` — are shown and not writable on purpose: a patient saying
#: the chart has their name or their birthday wrong is the start of a
#: conversation with the practice, not a form submission, and the intake form
#: already collects exactly that correction for a clinician to act on.
PATIENT_SELF_WRITABLE_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "preferred_name",
        "email",
        "phone",
        "address_line1",
        "address_line2",
        "city",
        "state",
        "postal_code",
    }
)

#: Of those, the two that are also DELIVERY CHANNELS — the address an invite
#: link is emailed to and the number a step-up code is texted to. Changing one
#: changes where a future credential goes, so the route audits them
#: separately, with hashes rather than values.
PATIENT_CONTACT_CHANNEL_COLUMNS: Final[frozenset[str]] = frozenset({"email", "phone"})


__all__ = [
    "APPOINTMENT_COLUMN_DECISIONS",
    "PATIENT_COLUMN_DECISIONS",
    "PATIENT_CONTACT_CHANNEL_COLUMNS",
    "PATIENT_SELF_WRITABLE_COLUMNS",
    "SHOWN",
    "PatientAppointmentResponse",
    "PatientFacingPatient",
    "shown_columns",
    "withheld_columns",
]
