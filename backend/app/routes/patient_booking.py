# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A patient booking their own appointment.

The write half of the patient calendar. ``patient_appointments.py`` lets a
patient see what they already have; this lets them add to it, within whatever
the practice has said it allows.

**Two sessions per request, each with exactly one principal.** This is the
whole design, and getting it wrong is the failure mode this module exists to
avoid:

* The **patient-armed** session — the one ``get_patient_context`` set up —
  reads the patient's own rows and writes their audit entries. It sees only
  this patient.
* A separate **owner-armed** session does everything that must see the WHOLE
  diary: computing free slots, and the conflict check inside a booking. A slot
  is free precisely because nobody else holds it, so that computation has to
  read every appointment in the practice.

Arming both GUCs on one session would be the shortcut, and it is the bug. Under
the patient GUC alone the conflict check sees only the caller's own
appointments — so every hour another patient holds looks free, the engine
offers it, and two people are booked into the same slot. The review of the
patient principal found six defects of exactly that one class.

**Nothing the owner-armed session reads reaches the response.** It exists to
answer "is this time taken", and the answer that travels is a time, never the
appointment that made it unavailable. :class:`PatientSlotResponse` carries
three fields for that reason.

**Dark by default.** Every route here refuses unless the practice has turned
self-booking on. A practice that has not thought about patient booking has not
thereby agreed to it, so the default is off — and the refusal is a 403 rather
than an empty list, because "you may not" and "there is nothing free" are
different answers and a patient should be able to tell them apart.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from ..auth.patient_context import AuthStrength, PatientContext, get_patient_context
from ..auth.route_access import subscription_exempt
from ..db import arm_current_user_id, create_standalone_session, set_tenant_schema
from ..models.audit import AuditAction, ResourceType
from ..models.scheduling import (
    PatientAppointmentResponse,
    PatientBookingRequest,
    PatientRescheduleRequest,
    PatientSlotListResponse,
    PatientSlotResponse,
)
from ..repositories.postgres.appointment import PostgresAppointmentRepository
from ..repositories.postgres.appointment_type import PostgresAppointmentTypeRepository
from ..repositories.postgres.availability_rule import PostgresAvailabilityRuleRepository
from ..repositories.postgres.user import PostgresUserRepository
from ..scheduling_engine.exceptions import (
    AppointmentConflictError,
    AppointmentNotFoundError,
    InvalidAppointmentError,
    RuleViolationError,
)
from ..scheduling_engine.models.appointment import AppointmentStatus
from ..scheduling_engine.services.availability import AvailabilityEngine
from ..scheduling_engine.services.scheduling import SchedulingService
from ..scheduling_engine.services.scheduling_policy import load_policy, may_self_book
from ..services.audit_service import AuditService, get_audit_service

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import tzinfo

    from sqlalchemy.orm import Session

    from ..scheduling_engine.models.appointment import Appointment
    from ..scheduling_engine.models.appointment_type import AppointmentType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/patient/booking", tags=["patient-booking"])

CurrentPatient = Annotated[PatientContext, Depends(get_patient_context)]

#: What a patient may be told. Deliberately coarse: they learn that they may
#: not book and why in the practice's own terms, never anything about the diary
#: that made a particular time unavailable.
_SELF_BOOKING_OFF = "This practice does not offer online booking."
_TYPE_NOT_BOOKABLE = "That kind of appointment cannot be booked online."
_TOO_SOON = "That time is sooner than this practice accepts online bookings."
_TOO_FAR = "That date is further ahead than this practice takes bookings."
_SLOT_TAKEN = "That time is no longer available."
_NO_CLINICIAN = "This account is not set up for online booking yet."
_NO_SUCH_APPOINTMENT = "No such appointment."
_ALREADY_SETTLED = "That appointment can no longer be changed online."
_TOO_LATE_TO_CANCEL = "It is too close to the appointment to cancel it online."
_TOO_LATE_TO_RESCHEDULE = "It is too close to the appointment to move it online."

#: Fallback when an appointment type carries no length of its own.
_DEFAULT_DURATION_MINUTES = 50


def _refuse(message: str, code: str, http_status: int) -> HTTPException:
    return HTTPException(
        status_code=http_status,
        detail={"error": {"code": code, "message": message, "details": {}}},
    )


def _as_utc(value: datetime) -> datetime:
    """A timezone-aware UTC instant.

    A naive datetime off the wire is read as UTC rather than rejected: the
    comparisons below are all against ``datetime.now(UTC)``, and comparing a
    naive value to an aware one raises rather than returning a wrong answer, so
    the failure would be a 500 on a field the caller controls.
    """
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _slot_instant(iso: str) -> datetime:
    """One end of a ``TimeSlot``, which the engine hands back as an ISO string.

    ``Z`` is spelled out for ``fromisoformat`` because the engine renders UTC
    that way and Python did not accept it before 3.11 — the substitution is
    what every other reader of these slots does, and doing it differently here
    would be the interesting kind of inconsistency.
    """
    return _as_utc(datetime.fromisoformat(iso.replace("Z", "+00:00")))


@contextlib.contextmanager
def owner_session(patient: PatientContext) -> Iterator[tuple[Session, str]]:
    """A second session, armed as the clinician whose diary is in question.

    Separate from the request's patient-armed session on purpose — see the
    module docstring. Yields ``(session, clinician_user_id)``.

    The clinician is the practice's owner. For the single-clinician practice
    this serves, that is exactly right, and it is the only answer available
    without widening what a patient may read: ``patient_clinicians`` is gated
    on the clinician's own GUC, so a patient cannot be asked who their
    clinician is. Booking with a chosen clinician in a multi-clinician practice
    needs that seam opened deliberately and is not this route's to assume.

    ``platform.practices`` carries no row policies, which is what makes it a
    non-circular place to find a principal.
    """
    from ..db.platform_models import PracticeRow

    session = create_standalone_session()
    try:
        practice = (
            session.query(PracticeRow)
            .filter(PracticeRow.schema_name == patient.practice_schema)
            .one_or_none()
        )
        owner = practice.owner_user_id if practice is not None else None
        if not owner:
            raise _refuse(_NO_CLINICIAN, "NO_CLINICIAN", status.HTTP_409_CONFLICT)

        set_tenant_schema(session, patient.practice_schema)
        arm_current_user_id(session, owner)
        yield session, owner
    finally:
        session.close()


def _owner_timezone(session: Session, owner: str) -> tzinfo:
    """The clinician's own timezone — the frame their availability rules mean.

    Working hours, blocked days and per-day caps are all expressed in the
    clinician's local time, and ``get_free_slots`` takes the calendar date in
    that frame. Defaulting to UTC (which is what omitting ``tz`` does) silently
    computes a different day for every practice that is not on UTC: a London
    clinician's Monday 09:00-17:00 is evaluated against a UTC Monday, and in
    summer the two disagree by an hour at both ends.

    Falls back to UTC on an unresolvable preference rather than failing the
    request, matching ``routes.scheduling._owner_timezone``. The raw preference
    string stays out of logs — it is caller-controlled.
    """
    try:
        raw = PostgresUserRepository(session).get_preferences(owner).timezone
        return ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError, AttributeError):
        logger.warning("patient booking: unresolvable timezone for owner %s, using UTC", owner)
        return UTC


def _require_stepped_up(patient: PatientContext) -> None:
    """Refuse a single-factor principal on a route that changes the diary.

    ``AuthStrength`` is recorded by the resolver and enforced by each route,
    and this is the first route to need the strong half. Booking creates a real
    appointment in a clinician's calendar; cancelling destroys one. An invite
    link that reached the wrong inbox is one factor in a stranger's hands, and
    "somebody who found a forwarded email filled the therapist's Tuesday" is
    not a failure mode worth accepting for the convenience of skipping a code.

    Reading is deliberately not gated this way — ``patient_appointments`` lets a
    single-factor principal see their own times, because failing to show
    somebody their own appointment is its own harm and the blast radius of a
    read is one person's schedule. Writing is where the asymmetry bites.
    """
    if patient.auth_strength is not AuthStrength.STEPPED_UP:
        raise _refuse(
            "Confirm it is you before changing an appointment.",
            "STEP_UP_REQUIRED",
            status.HTTP_403_FORBIDDEN,
        )


def _require_self_booking(policy: dict[str, object]) -> None:
    """Refuse unless the practice lets an existing patient self-book.

    Keyed on ``self_book_existing``: everyone reaching these routes is an
    authenticated patient of this practice, so they are by definition not the
    ``self_book_new`` case, which governs a stranger booking a first
    appointment through a public surface.
    """
    if not may_self_book(policy, is_new_patient=False):
        raise _refuse(_SELF_BOOKING_OFF, "SELF_BOOKING_DISABLED", status.HTTP_403_FORBIDDEN)


def _bookable_type(session: Session, owner: str, session_type: str) -> AppointmentType:
    """The named appointment type, if the practice marked it self-bookable.

    An allow-list, not a deny-list. A type nobody opted in is not bookable, so
    a practice that turns the master switch on without choosing types has
    opened nothing — which is the safe direction. The alternative makes every
    appointment type patient-bookable the moment the switch flips, including
    ones that exist for internal bookkeeping.
    """
    for candidate in PostgresAppointmentTypeRepository(session).list_by_user(owner):
        if candidate.name == session_type and candidate.self_bookable:
            return candidate
    raise _refuse(_TYPE_NOT_BOOKABLE, "TYPE_NOT_BOOKABLE", status.HTTP_403_FORBIDDEN)


def _own_appointment(
    service: SchedulingService, owner: str, patient: PatientContext, appointment_id: str
) -> Appointment:
    """One appointment, only if it belongs to the calling patient.

    This is the load-bearing check on both change routes, and the reason they
    cannot simply hand the path id to the service. ``get_appointment`` and
    ``cancel_appointment`` are keyed on the CLINICIAN's user id, which on the
    owner-armed session is satisfied by every appointment in the practice. Pass
    the id through unchecked and a patient can cancel or move a stranger's
    Tuesday by guessing a uuid — the same "two principals on one transaction"
    shape the slot computation has, arriving through the write path instead.

    A patient's own row and a row belonging to somebody else are both answered
    with 404. Distinguishing them would turn this route into an oracle for
    which appointment ids exist in the practice, which is worth more to an
    attacker than the row itself.
    """
    try:
        appointment = service.get_appointment(appointment_id, owner)
    except AppointmentNotFoundError as exc:
        raise _refuse(_NO_SUCH_APPOINTMENT, "NOT_FOUND", status.HTTP_404_NOT_FOUND) from exc
    if str(appointment.patient_id) != str(patient.patient_id):
        raise _refuse(_NO_SUCH_APPOINTMENT, "NOT_FOUND", status.HTTP_404_NOT_FOUND)
    return appointment


def _require_changeable(appointment: Appointment) -> None:
    """Refuse an appointment that has already been settled one way or another.

    Only a PENDING request or a CONFIRMED booking is still a future
    arrangement a patient can change. Cancelling an already-cancelled row is a
    no-op worth refusing rather than reporting as success, and a COMPLETED or
    NO_SHOW row is a clinical record of something that already happened —
    letting a patient rewrite its time would edit history, not a schedule.
    """
    if appointment.status not in {AppointmentStatus.PENDING, AppointmentStatus.CONFIRMED}:
        raise _refuse(_ALREADY_SETTLED, "NOT_CHANGEABLE", status.HTTP_409_CONFLICT)


def _require_outside_cutoff(
    appointment: Appointment, *, cutoff_hours: int, message: str, code: str, now: datetime
) -> None:
    """Refuse a change made too close to the appointment itself.

    The practice's own number, not a constant here: a clinician who holds a
    slot open needs to know when it stops being reclaimable, and 24 hours is a
    default rather than a rule.

    An appointment already in the past is inside every non-negative cutoff, so
    this covers "you cannot cancel last Tuesday" without a separate check.
    """
    if _as_utc(appointment.start_at) - now < timedelta(hours=cutoff_hours):
        raise _refuse(message, code, status.HTTP_409_CONFLICT)


def _window(policy: dict[str, object], *, now: datetime) -> tuple[datetime, datetime]:
    """The earliest and latest instants this practice will take a booking for."""
    min_notice = int(policy["min_notice_hours"])  # type: ignore[call-overload]
    max_horizon = int(policy["max_horizon_days"])  # type: ignore[call-overload]
    return now + timedelta(hours=min_notice), now + timedelta(days=max_horizon)


def _require_inside_window(policy: dict[str, object], start_at: datetime, *, now: datetime) -> None:
    """Refuse a time outside the practice's notice and horizon windows.

    Shared by booking and rescheduling rather than written twice. Moving an
    appointment is placing a booking at the new time, so it answers to the same
    two limits; a reschedule route that skipped them would be a way to reach
    tomorrow morning through the back door of an appointment booked properly
    last month.
    """
    earliest, latest = _window(policy, now=now)
    if start_at < earliest:
        raise _refuse(_TOO_SOON, "INSIDE_NOTICE_WINDOW", status.HTTP_409_CONFLICT)
    if start_at > latest:
        raise _refuse(_TOO_FAR, "OUTSIDE_HORIZON", status.HTTP_409_CONFLICT)


def _require_offered_slot(
    engine: AvailabilityEngine,
    owner: str,
    start_at: datetime,
    duration_minutes: int,
    *,
    tz: tzinfo,
) -> None:
    """Refuse a start the engine would not have offered.

    ``public_booking`` states the rule this implements: the client is never
    trusted about availability. A patient's request carries an arbitrary
    instant, and without this the only thing between it and the diary is a raw
    overlap test — so 03:00 on a blocked holiday books cleanly, because nothing
    else is there.

    Checked against the same computation ``GET /slots`` runs, in the clinician's
    own timezone, so the two endpoints cannot disagree about what is bookable.
    That symmetry is the point: an endpoint that offers a time it would then
    refuse, or refuses one it offered, is worse than either being wrong
    consistently.

    Soft rules still permit the booking — that is what soft means — but they
    reach the engine now, which is what makes ``over_cap`` and the warning list
    meaningful rather than dead.
    """
    date_str = start_at.astimezone(tz).date().isoformat()
    free = engine.get_free_slots(owner, date_str, duration_minutes, tz=tz)
    if not any(_slot_instant(slot.start) == start_at for slot in free.slots):
        raise _refuse(_SLOT_TAKEN, "SLOT_TAKEN", status.HTTP_409_CONFLICT)


def _to_patient_view(appointment: Appointment) -> PatientAppointmentResponse:
    """Project an appointment down to what its patient may see.

    Field by field rather than ``model_validate``, for the same reason
    ``patient_appointments._to_patient_view`` does it: a future column on the
    domain model must not become a response field by default.
    """
    return PatientAppointmentResponse(
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
    )


@router.get("/slots", response_model=PatientSlotListResponse)
def list_bookable_slots(
    request: Request,
    patient: CurrentPatient,
    date: Annotated[str, Query(description="Local calendar date, YYYY-MM-DD.")],
    duration_minutes: Annotated[int | None, Query(ge=5, le=240)] = None,
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> PatientSlotListResponse:
    """Openings a patient could book on one date.

    Computed on the owner-armed session, because a slot is only free relative
    to the whole diary, then narrowed to the practice's notice and horizon
    windows. Offering a slot the booking endpoint would refuse is worse than
    not offering it, so the same window is applied in both places.

    ``configured`` is not surfaced. The engine can tell "this clinician has set
    no hours" from "the hours leave nothing open today"; to a patient both mean
    nothing to book, and saying which would leak whether the practice has
    finished setting itself up.
    """
    with owner_session(patient) as (session, owner):
        policy = load_policy(session)
        _require_self_booking(policy)

        engine = AvailabilityEngine(
            PostgresAvailabilityRuleRepository(session),
            PostgresAppointmentRepository(session),
        )
        tz = _owner_timezone(session, owner)
        free = engine.get_free_slots(owner, date, duration_minutes, tz=tz)

        earliest, latest = _window(policy, now=datetime.now(UTC))
        offered = [
            PatientSlotResponse(
                start_at=start,
                end_at=end,
                duration_minutes=int((end - start).total_seconds() // 60),
            )
            for start, end in (
                (_slot_instant(slot.start), _slot_instant(slot.end)) for slot in free.slots
            )
            if earliest <= start <= latest
        ]

    # Audited as a read of this patient's booking surface. No resource of
    # theirs is involved, so the date stands in for one — naming any of the
    # rows that shaped the answer would defeat the point of the route.
    audit.log_patient_principal_action(
        action=AuditAction.APPOINTMENT_VIEWED,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.APPOINTMENT,
        resource_id=f"slots:{date}",
    )
    return PatientSlotListResponse(data=offered, total=len(offered))


@router.post("", response_model=PatientAppointmentResponse, status_code=status.HTTP_201_CREATED)
def book_appointment(
    request: Request,
    payload: PatientBookingRequest,
    patient: CurrentPatient,
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> PatientAppointmentResponse:
    """Book one appointment for the calling patient.

    ``patient_id`` comes from the principal and nothing else. The request model
    has no field for it, so there is no id to smuggle and no comparison for
    anyone to forget.

    The conflict check runs on the owner-armed session inside the same
    transaction as the insert. On the patient's session it would see only the
    caller's own appointments, so every hour another patient holds would look
    free and the booking would land on top of it.

    ``self_book_mode`` decides what the row starts as: ``request`` — the
    default — creates it PENDING with a hold that lapses, so the practice sees
    a request rather than a commitment; ``auto`` books it outright.
    """
    _require_stepped_up(patient)
    with owner_session(patient) as (session, owner):
        policy = load_policy(session)
        _require_self_booking(policy)
        appointment_type = _bookable_type(session, owner, payload.session_type)

        start_at = _as_utc(payload.start_at)
        _require_inside_window(policy, start_at, now=datetime.now(UTC))

        duration = (
            payload.duration_minutes
            or appointment_type.duration_minutes
            or _DEFAULT_DURATION_MINUTES
        )
        end_at = start_at + timedelta(minutes=duration)

        auto = policy.get("self_book_mode") == "auto"
        data: dict[str, str | int | datetime | None] = {
            "patient_id": patient.patient_id,
            "title": payload.session_type,
            "start_at": start_at,
            "end_at": end_at,
            "duration_minutes": duration,
            "session_type": payload.session_type,
            "appointment_type_id": appointment_type.id,
        }
        if not auto:
            # A held slot has to stop being held. The practice's own
            # pending_hold_hours decides when, capped at the appointment itself
            # — holding past the start time would be holding nothing.
            hold_hours = int(policy["pending_hold_hours"])  # type: ignore[call-overload]
            data["status"] = "pending"
            data["pending_expires_at"] = min(
                datetime.now(UTC) + timedelta(hours=hold_hours), start_at
            )

        tz = _owner_timezone(session, owner)
        engine = AvailabilityEngine(
            PostgresAvailabilityRuleRepository(session),
            PostgresAppointmentRepository(session),
        )

        # The client is never trusted about availability. Two independent
        # guards, because they fail differently:
        #
        # 1. The instant must be one the engine actually offered. Without this
        #    a patient can POST any time at all — the listing endpoint honours
        #    the rules, so the route LOOKS correct in any test that books a slot
        #    it was offered, which is exactly what every test here does.
        # 2. The service gets the engine, so it applies the rules itself. Built
        #    without one, ``_check_availability_rules`` short-circuits and the
        #    only check left is a raw overlap test — every rule type, at both
        #    enforcement levels, silently bypassed.
        #
        # Guard 2 is a backstop, and deliberately an unobservable one today:
        # deleting it leaves this suite green, because anything it would refuse
        # guard 1 has already refused. That is a fact about the pair, not a
        # missing test — the two gates are only redundant while the offered-slot
        # lattice stays a strict subset of what the rules permit, and it is the
        # cheap half of the pair to keep correct. Left in place because the day
        # guard 1 is relaxed for a duration or a slot-hint the lattice does not
        # model, guard 2 is the only thing between a patient's arbitrary instant
        # and the diary.
        _require_offered_slot(engine, owner, start_at, duration, tz=tz)

        try:
            created = SchedulingService(
                PostgresAppointmentRepository(session), engine
            ).create_appointment(owner, data=data, tz=tz)
        except AppointmentConflictError as exc:
            # Somebody took it between the slot listing and this request, or the
            # caller never looked. Either way the answer is the same, and it
            # says nothing about who holds it.
            raise _refuse(_SLOT_TAKEN, "SLOT_TAKEN", status.HTTP_409_CONFLICT) from exc
        except RuleViolationError as exc:
            # A hard rule refused it — blocked leave, a per-day cap, a buffer.
            # The patient is told the time is unavailable and nothing about
            # which rule: why a clinician's Tuesday is closed is not theirs.
            raise _refuse(_SLOT_TAKEN, "SLOT_TAKEN", status.HTTP_409_CONFLICT) from exc
        except InvalidAppointmentError as exc:
            raise _refuse(str(exc), "INVALID_BOOKING", status.HTTP_400_BAD_REQUEST) from exc

        session.commit()

    audit.log_patient_principal_action(
        action=AuditAction.APPOINTMENT_CREATED,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.APPOINTMENT,
        resource_id=created.id,
    )
    return _to_patient_view(created)


@router.post("/{appointment_id}/reschedule", response_model=PatientAppointmentResponse)
def reschedule_appointment(
    request: Request,
    appointment_id: str,
    payload: PatientRescheduleRequest,
    patient: CurrentPatient,
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> PatientAppointmentResponse:
    """Move one of the calling patient's own appointments to a different time.

    A reschedule is a cancellation and a booking that must not be separable —
    a patient who gives up their Tuesday should not be able to discover that
    Thursday was gone all along and now they have neither. So the new time is
    vetted BEFORE anything is written, against the same notice window, horizon
    and offered-slot check a fresh booking answers to, and the row moves in one
    transaction or not at all.

    Both ends are gated, and they are different gates:

    * the OLD time answers to ``reschedule_cutoff_hours`` — past that, the
      clinician has arranged their day around it and reclaiming the slot is a
      conversation, not a form;
    * the NEW time answers to the full booking policy, because placing an
      appointment there is placing a booking there.

    The kind of appointment does not change. Only the time is in the request,
    so a short check-in cannot be converted into a long slot the practice never
    opened, and the existing duration travels with the row.
    """
    _require_stepped_up(patient)
    now = datetime.now(UTC)
    with owner_session(patient) as (session, owner):
        policy = load_policy(session)
        _require_self_booking(policy)

        tz = _owner_timezone(session, owner)
        engine = AvailabilityEngine(
            PostgresAvailabilityRuleRepository(session),
            PostgresAppointmentRepository(session),
        )
        service = SchedulingService(PostgresAppointmentRepository(session), engine)

        appointment = _own_appointment(service, owner, patient, appointment_id)
        _require_changeable(appointment)
        _require_outside_cutoff(
            appointment,
            cutoff_hours=int(policy["reschedule_cutoff_hours"]),  # type: ignore[call-overload]
            message=_TOO_LATE_TO_RESCHEDULE,
            code="INSIDE_RESCHEDULE_CUTOFF",
            now=now,
        )

        start_at = _as_utc(payload.start_at)
        if start_at == _as_utc(appointment.start_at):
            # Asking for the time it already has. Answering SLOT_TAKEN here
            # would be true and useless — the thing holding the slot is this
            # very appointment — so the honest answer is the unchanged row.
            return _to_patient_view(appointment)

        duration = appointment.duration_minutes or _DEFAULT_DURATION_MINUTES
        _require_inside_window(policy, start_at, now=now)

        # Same pair of guards the booking path uses, for the same reason: the
        # client is never trusted about availability, and a reschedule is a
        # booking. ``update_appointment`` excludes this row from the OVERLAP
        # check, so moving within the diary does not collide with itself — but
        # it does not exclude it from the RULE check, so a practice running
        # buffers can find an appointment's own buffer blocking the slot next
        # to it. That is existing engine behaviour, shared with the
        # clinician-side reschedule, and is not worked around here.
        _require_offered_slot(engine, owner, start_at, duration, tz=tz)

        try:
            moved = service.update_appointment(
                appointment_id,
                owner,
                tz=tz,
                start_at=start_at,
                end_at=start_at + timedelta(minutes=duration),
            )
        except AppointmentConflictError as exc:
            raise _refuse(_SLOT_TAKEN, "SLOT_TAKEN", status.HTTP_409_CONFLICT) from exc
        except RuleViolationError as exc:
            raise _refuse(_SLOT_TAKEN, "SLOT_TAKEN", status.HTTP_409_CONFLICT) from exc
        except InvalidAppointmentError as exc:
            raise _refuse(str(exc), "INVALID_BOOKING", status.HTTP_400_BAD_REQUEST) from exc

        session.commit()

    audit.log_patient_principal_action(
        action=AuditAction.APPOINTMENT_UPDATED,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.APPOINTMENT,
        resource_id=moved.id,
    )
    return _to_patient_view(moved)


@router.post("/{appointment_id}/cancel", response_model=PatientAppointmentResponse)
def cancel_appointment(
    request: Request,
    appointment_id: str,
    patient: CurrentPatient,
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> PatientAppointmentResponse:
    """Cancel one of the calling patient's own appointments.

    POST rather than DELETE, because nothing is deleted. The row survives as a
    CANCELLED appointment: it is part of the clinical record of the
    relationship, it is what a late-cancellation policy is applied to, and the
    slot it frees is freed by the status change alone — availability treats
    everything that is not cancelled as busy.

    Gated on ``self_book_existing`` like every other route in this module. A
    practice that has not turned online booking on has not agreed to online
    cancellation either, and there is no separate flag to read: the safe
    reading of "unconfigured" stays "not allowed". If cancelling should be
    available to practices that book their patients themselves — a defensible
    position, since the alternative to an easy cancellation is a no-show —
    that is a new policy field and a deliberate decision, not something to
    infer here.
    """
    _require_stepped_up(patient)
    now = datetime.now(UTC)
    with owner_session(patient) as (session, owner):
        policy = load_policy(session)
        _require_self_booking(policy)

        service = SchedulingService(PostgresAppointmentRepository(session))
        appointment = _own_appointment(service, owner, patient, appointment_id)
        _require_changeable(appointment)
        _require_outside_cutoff(
            appointment,
            cutoff_hours=int(policy["cancel_cutoff_hours"]),  # type: ignore[call-overload]
            message=_TOO_LATE_TO_CANCEL,
            code="INSIDE_CANCEL_CUTOFF",
            now=now,
        )

        cancelled = service.cancel_appointment(appointment_id, owner)
        session.commit()

    audit.log_patient_principal_action(
        action=AuditAction.APPOINTMENT_CANCELLED,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.APPOINTMENT,
        resource_id=cancelled.id,
    )
    return _to_patient_view(cancelled)
