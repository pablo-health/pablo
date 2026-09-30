# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Follow the sessions another service puts on a calendar Pablo reads.

Many clinicians already have their sessions put on their main calendar by
another scheduling service, or published in a calendar feed. Pablo follows
them: each one that looks like a session is held as an open row
(``external_calendar_events``) and asked about once per client — "is this a
client? which one?" — and the answer is remembered through
``app.patients.matching``, so the next event for the same client becomes an
appointment without asking.

What becomes a row is deliberately narrow, because a dentist appointment must
never ask "is this a client?". An event is held when it repeats, when the
matcher finds a patient it could be (a name alone is a question here, not an
answer), or when its identifier is already remembered as a client. Anything
else stays a busy block. An identifier remembered as not a client never
becomes a row.

An open row is not an appointment: ``appointments.patient_id`` stays required,
and nothing here writes an appointment without one. An answered row's
appointment follows its event — see ``google_calendar_follow`` for the guards
on moves and deletions. Nothing here ever writes to the calendar the event
came from.

HIPAA: event titles often carry a client's name. They stay on the row and the
chart they are confirmed to; logs carry counts only.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC
from typing import TYPE_CHECKING, Any

from ..calendar_providers.source_identity import (
    GOOGLE_CALENDAR_SOURCE,
    SERIES_PREFIX,
    event_source_identifier,
    ical_feed,
)
from ..patients.matching import (
    MatchContext,
    MatchResult,
    PatientHint,
    match_patient,
    normalize,
    remember_match,
    remember_not_a_client,
)
from ..repositories.external_calendar_event import (
    ANSWER_CLIENT,
    ANSWER_OPEN,
    ExternalCalendarEvent,
)
from ..scheduling_engine.models.appointment import Appointment, AppointmentStatus
from ..utcnow import utc_now
from .google_calendar_follow import UPCOMING_HORIZON
from .google_calendar_service import parse_event_time

if TYPE_CHECKING:
    from datetime import datetime, tzinfo

    from ..repositories.external_calendar_event import ExternalCalendarEventRepository
    from ..repositories.patient import PatientRepository
    from ..repositories.patient_source_mapping import PatientSourceMappingRepository
    from ..scheduling_engine.repositories.appointment import AppointmentRepository

logger = logging.getLogger(__name__)

#: What an appointment made for a followed session is called. The patient's
#: name is what the calendar shows; the event's own title is not copied.
APPOINTMENT_TITLE = "Session"


@dataclass
class Question:
    """One "who is this?" — every open event with the same identifier."""

    source: str
    source_identifier: str
    title: str
    next_start_at: datetime
    recurring: bool
    match: MatchResult
    suggested_patient_id: str | None = None
    """A remembered answer offered for confirmation rather than acted on."""
    rows: list[ExternalCalendarEvent] = field(default_factory=list)


@dataclass
class Ingested:
    """What one read of a calendar did."""

    held: int = 0
    """Rows held open, refreshed, or answered from a remembered client."""
    booked: list[Appointment] = field(default_factory=list)
    """Appointments made for remembered clients, without asking."""


@dataclass(frozen=True)
class _Identity:
    """How an event's client is remembered, and what the event says about them."""

    mapping_source: str
    identifier: str
    hint: PatientHint


class OutsideSessions:
    def __init__(
        self,
        events: ExternalCalendarEventRepository,
        appointments: AppointmentRepository,
        patients: PatientRepository,
        mappings: PatientSourceMappingRepository,
        *,
        zone: tzinfo = UTC,
    ) -> None:
        self._events = events
        self._appointments = appointments
        self._patients = patients
        self._mappings = mappings
        # The clinician's own zone: an event without a series is remembered
        # by the weekday and time it falls on there, as the import does.
        self._zone = zone

    def in_zone(self, zone: tzinfo) -> OutsideSessions:
        """The same sessions, read in one clinician's zone."""
        return OutsideSessions(
            self._events, self._appointments, self._patients, self._mappings, zone=zone
        )

    def context(self, user_id: str) -> MatchContext:
        return MatchContext.for_clinician(user_id, self._patients, self._mappings)

    # --- Reading a calendar ------------------------------------------------

    def ingest_google(self, user_id: str, changes: list[dict[str, Any]]) -> Ingested:
        """Hold, refresh, answer or drop a row for each main-calendar change.

        Moves and deletions of an answered session's appointment are the
        follower's, not this.
        """
        ctx = self.context(user_id)
        result = Ingested()
        for change in changes:
            event_id = str(change.get("google_event_id") or "")
            if not event_id:
                continue
            row = self._events.get(user_id, GOOGLE_CALENDAR_SOURCE, event_id)
            start = parse_event_time(change.get("start") or {})
            end = parse_event_time(change.get("end") or {})
            if change.get("status") == "cancelled" or start is None or end is None:
                if row is not None:
                    self._events.delete(user_id, row.id)
                continue
            incoming = ExternalCalendarEvent(
                id=row.id if row else str(uuid.uuid4()),
                user_id=user_id,
                source=GOOGLE_CALENDAR_SOURCE,
                source_event_id=event_id,
                source_series_id=change.get("series_id"),
                start_at=start,
                end_at=end,
                title=str(change.get("summary") or ""),
            )
            result.held += self._place(incoming, row, ctx, result.booked)
        logger.info(
            "Followed %d outside sessions from the main calendar, booked %d",
            result.held,
            len(result.booked),
        )
        return result

    def _place(
        self,
        incoming: ExternalCalendarEvent,
        row: ExternalCalendarEvent | None,
        ctx: MatchContext,
        booked: list[Appointment],
    ) -> int:
        identity = self._identity(incoming)
        match = match_patient(identity.hint, ctx, name_alone_is_enough=False)
        if match.evidence == "not_a_client":
            if row is not None:
                self._events.delete(incoming.user_id, row.id)
            return 0
        if row is not None:
            # Answers and appointment links are the row's own; the calendar
            # only says where and what the event is now.
            incoming.answer = row.answer
            incoming.patient_id = row.patient_id
            incoming.appointment_id = row.appointment_id
            incoming.created_at = row.created_at
        if (
            match.evidence == "remembered"
            and match.patient_id
            and _books_unattended(incoming, identity)
        ):
            if incoming.answer == ANSWER_OPEN:
                incoming.answer = ANSWER_CLIENT
                incoming.patient_id = match.patient_id
                appointment = self._book(incoming)
                if appointment is not None:
                    booked.append(appointment)
            self._events.save(incoming)
            return 1
        qualifies = bool(
            incoming.source_series_id
            or match.patient_id
            or match.possible_ids
            # Remembered as a client whose chart is gone: ask again.
            or _remembered(ctx, identity)
        )
        if incoming.answer == ANSWER_OPEN and not qualifies:
            if row is not None:
                self._events.delete(incoming.user_id, row.id)
            return 0
        self._events.save(incoming)
        return 1

    # --- A calendar feed -----------------------------------------------------

    def hold(self, event: ExternalCalendarEvent) -> None:
        """Keep a feed event nobody has matched as an open row, or refresh it."""
        row = self._events.get(event.user_id, event.source, event.source_event_id)
        if row is not None:
            event.id = row.id
            event.answer = row.answer
            event.patient_id = row.patient_id
            event.appointment_id = row.appointment_id
            event.created_at = row.created_at
        self._events.save(event)

    def settle(self, user_id: str, source: str, event_id: str, appointment: Appointment) -> None:
        """A feed event was matched and booked on its own; its open row is answered."""
        row = self._events.get(user_id, source, event_id)
        if row is None:
            return
        row.answer = ANSWER_CLIENT
        row.patient_id = appointment.patient_id
        row.appointment_id = appointment.id
        self._events.save(row)

    def forget(self, user_id: str, source: str, *, keep: set[str]) -> None:
        """Drop the rows of events no longer in the feed."""
        for row in self._events.list_by_source(user_id, source):
            if row.source_event_id not in keep:
                self._events.delete(user_id, row.id)

    def reconcile_full_read(self, user_id: str, present: set[str]) -> list[dict[str, Any]]:
        """Catch up with a full read of the main calendar.

        A full read (see ``MainCalendarRead.full``) holds every event still to
        come, and never reports what was deleted before it. So a row for an
        upcoming event it doesn't hold is for an event that is gone, and goes.
        An appointment following such an event is not cancelled here: it comes
        back as a deletion for the follower, so the bulk guard decides — a
        reset that seems to lose everything is held, never mass-cancelled.
        """
        now = utc_now()
        for row in self._events.list_by_source(user_id, GOOGLE_CALENDAR_SOURCE):
            if row.end_at > now and row.source_event_id not in present:
                self._events.delete(user_id, row.id)
        return [
            {"google_event_id": appointment.outside_event_id, "status": "cancelled"}
            for appointment in self._appointments.list_by_range(
                user_id, now, now + UPCOMING_HORIZON
            )
            if appointment.outside_source == GOOGLE_CALENDAR_SOURCE
            and appointment.status == AppointmentStatus.CONFIRMED
            and appointment.outside_event_id not in present
        ]

    def drop_open(self, user_id: str, source: str) -> None:
        """Drop every question from a source, leaving answered sessions as they are."""
        for row in self._events.list_by_source(user_id, source):
            if row.answer == ANSWER_OPEN:
                self._events.delete(user_id, row.id)

    def drop(self, user_id: str, source: str, event_id: str) -> None:
        row = self._events.get(user_id, source, event_id)
        if row is not None:
            self._events.delete(user_id, row.id)

    # --- Asking and answering ----------------------------------------------

    def open_sessions(
        self,
        user_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
        *,
        hidden: frozenset[str] = frozenset(),
    ) -> list[tuple[ExternalCalendarEvent, str]]:
        """Open rows, each with the identifier its answer is remembered under.

        ``hidden`` leaves out the sources not being followed right now.
        """
        return [
            (row, self._identity(row).identifier)
            for row in self._events.list_open(user_id, start, end)
            if row.source not in hidden
        ]

    def questions(self, user_id: str, *, hidden: frozenset[str] = frozenset()) -> list[Question]:
        """One question per identifier with open rows, soonest first.

        A unique name match is offered as the answer to confirm — here it is
        a suggestion, and the clinician still says yes. So is a remembered
        answer for a slot rather than a provider series (``suggested``): the
        same weekday and time can be someone else now.
        """
        ctx = self.context(user_id)
        by_key: dict[tuple[str, str], Question] = {}
        for row in self._events.list_open(user_id):
            if row.source in hidden:
                continue
            identity = self._identity(row)
            question = by_key.get((row.source, identity.identifier))
            if question is None:
                match = match_patient(identity.hint, ctx)
                suggested = None
                if match.evidence == "remembered" and match.patient_id:
                    suggested = match.patient_id
                    match = MatchResult(possible_ids=[match.patient_id])
                question = Question(
                    source=row.source,
                    source_identifier=identity.identifier,
                    title=row.title,
                    next_start_at=row.start_at,
                    recurring=bool(row.source_series_id),
                    match=match,
                    suggested_patient_id=suggested,
                )
                by_key[(row.source, identity.identifier)] = question
            question.rows.append(row)
        return list(by_key.values())

    def open_rows(
        self, user_id: str, source: str, source_identifier: str
    ) -> list[ExternalCalendarEvent]:
        """The open rows one answer would settle."""
        return [
            row
            for row in self._events.list_by_source(user_id, source)
            if row.answer == ANSWER_OPEN and self._identity(row).identifier == source_identifier
        ]

    def answer(
        self,
        user_id: str,
        source: str,
        source_identifier: str,
        *,
        patient_id: str | None,
        ctx: MatchContext | None = None,
    ) -> list[ExternalCalendarEvent]:
        """Settle every open row with this identifier, and remember the answer.

        ``patient_id`` None means not a client: remembered, and the rows go,
        leaving the events as busy blocks. A client books an appointment for
        each row that follows its event. Returns the rows answered.
        """
        ctx = ctx or self.context(user_id)
        rows = self.open_rows(user_id, source, source_identifier)
        mapping_source = _mapping_source(source)
        if patient_id is None:
            remember_not_a_client(mapping_source, source_identifier, ctx)
            for row in rows:
                self._events.delete(user_id, row.id)
            return rows
        remember_match(mapping_source, source_identifier, patient_id, ctx)
        for row in sorted(rows, key=lambda r: r.start_at):
            row.answer = ANSWER_CLIENT
            row.patient_id = patient_id
            self._book(row)
            self._events.save(row)
        return rows

    def _identity(self, row: ExternalCalendarEvent) -> _Identity:
        feed = ical_feed(row.source)
        if feed is not None:
            from .ical_sync_service import feed_identity

            identifier, hint = feed_identity(feed, row.title)
            return _Identity(feed, identifier, hint)
        identifier = event_source_identifier(
            row.source_series_id, row.title, row.start_at, self._zone
        )
        return _Identity(
            row.source,
            identifier,
            PatientHint(full_name=row.title, source=row.source, source_identifier=identifier),
        )

    def _book(self, row: ExternalCalendarEvent) -> Appointment | None:
        """Make the appointment an answered row follows.

        Skipped when something is already booked over it — most often the
        same session booked in Pablo as well — so the practice isn't double
        booked. The row is still answered, and so never asked about again.
        """
        if row.patient_id is None or row.appointment_id is not None:
            return None
        if self._appointments.list_overlapping(row.user_id, row.start_at, row.end_at):
            logger.info("An answered outside session overlaps a booking; not booked twice")
            return None
        feed = ical_feed(row.source)
        now = utc_now()
        appointment = self._appointments.create(
            Appointment(
                id=str(uuid.uuid4()),
                user_id=row.user_id,
                patient_id=row.patient_id,
                title=APPOINTMENT_TITLE,
                start_at=row.start_at,
                end_at=row.end_at,
                duration_minutes=int((row.end_at - row.start_at).total_seconds() // 60),
                status=AppointmentStatus.CONFIRMED,
                session_type="individual",
                outside_source=row.source,
                outside_event_id=row.source_event_id,
                # A feed's own sync follows these by uid, as it does the
                # sessions it matched itself.
                ical_uid=row.source_event_id if feed else None,
                ical_source=feed,
                ical_sync_status="synced" if feed else None,
                created_at=now,
                updated_at=now,
            )
        )
        row.appointment_id = appointment.id
        return appointment


def _books_unattended(row: ExternalCalendarEvent, identity: _Identity) -> bool:
    """Whether a remembered answer may book this event without asking.

    Only when the identifier can't be reused by someone else: a provider's
    own series id, or a feed's own client identifier. A slot (``shape:``) is
    reused — a year on, Monday 10:00 "Session" may be a different client — so
    a remembered slot is offered for one confirm, never booked unattended.
    """
    return identity.identifier.startswith(SERIES_PREFIX) or ical_feed(row.source) is not None


def _remembered(ctx: MatchContext, identity: _Identity) -> bool:
    return normalize(identity.identifier) in ctx.remembered(identity.mapping_source)


def _mapping_source(source: str) -> str:
    """Where an answer for this source is remembered.

    A feed keeps the name it has always remembered its clients under, so
    answers given before it was followed still count.
    """
    return ical_feed(source) or source


__all__ = ["APPOINTMENT_TITLE", "Ingested", "OutsideSessions", "Question"]
