# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Follow the sessions another service puts on a calendar Pablo reads.

Many clinicians already have their sessions put on their main calendar by
another scheduling service, or published in a calendar feed. Pablo follows
them: each one that looks like a session is held as an open row
(``external_calendar_events``) and asked about — "is this a client? which
one?" — and the answer is remembered through ``app.patients.matching``, so
the next event for the same client becomes an appointment without asking,
when the event can say which client it is.

**When a remembered answer books without asking** is ``unattended``, one rule
for every source. An identifier books on its own only while it can't have
been reused by someone else, and only onto an active chart:

* a provider's own series id (``series:``), while the series still carries
  the title it was answered under: editing every event of a Google series to
  another client's name keeps the id, so a changed title asks again;
* a feed's own client code (Sessions Health's ``SH00001``);
* a feed title that is a full name, when exactly one of the clinician's own
  charts bears it (middle names aside). A name two charts share, or a new
  client with an identical name, can't be told apart by the title.

Never: a slot (``shape:``, Monday 10:00 "Session" may be someone else a year
on), initials ("J.A." fits four clients on one real feed), or any chart that
is inactive or on hold — a session for an inactive client is the cue to make
them active again, which the question offers. Whatever is remembered is the
question's pre-fill: one tap to confirm or change. Initials and shared names
are asked about once per event, since each event could be a different
client; everything else is asked once per identifier.

What becomes a row is deliberately narrow, because a dentist appointment must
never ask "is this a client?". An event is held when it repeats, when the
matcher finds a patient it could be (a name alone is a question here, not an
answer), or when its identifier is already remembered as a client. Anything
else stays a busy block. An identifier remembered as not a client never
becomes a row.

**Whose answer it is.** A feed's client code or name is the clinician's own:
a Sessions Health code is numbered from their export, and two clinicians'
clients can share a name. A calendar's series is that calendar's, keyed by the
calendar the row was read from (``answer_scope``); a personal calendar has
one follower, and a shared calendar's answer is shared. A row from before
calendars were recorded came from the main calendar, so it falls back to
the main calendar the caller says it knows (``main_calendar_id``).

An open row is not an appointment: ``appointments.patient_id`` stays required,
and nothing here writes an appointment without one. **One outside event is
at most one live appointment in the practice**: an answer that finds the
event already booked — by a colleague following the same calendar, or by a
request that got there first — links its row to that appointment instead of
making another; the database's unique index is the race guard. An answered
row's appointment follows its event — see ``google_calendar_follow`` for the
guards on moves and deletions. Nothing here ever writes to the calendar the
event came from.

HIPAA: event titles often carry a client's name. They stay on the row and the
chart they are confirmed to; the title an answer was given under is kept as a
keyed digest; logs carry counts only.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, timedelta
from typing import TYPE_CHECKING, Any, Literal

from ..calendar_providers.practice_import import MAX_HORIZON_DAYS
from ..calendar_providers.source_identity import (
    GOOGLE_CALENDAR_SOURCE,
    SERIES_PREFIX,
    answer_scope,
    answered_title_digest,
    event_source_identifier,
    ical_feed,
)
from ..patients.matching import (
    NAME_ONLY,
    MatchContext,
    MatchResult,
    PatientHint,
    match_patient,
    remember_match,
    remember_not_a_client,
    same_name_charts,
)
from ..repositories.external_calendar_event import (
    ANSWER_CLIENT,
    ANSWER_NOT_A_CLIENT,
    ANSWER_OPEN,
    ExternalCalendarEvent,
)
from ..scheduling_engine.exceptions import OutsideEventAlreadyBookedError
from ..scheduling_engine.models.appointment import Appointment, AppointmentStatus
from ..utcnow import utc_now
from .google_calendar_service import parse_event_time

if TYPE_CHECKING:
    from datetime import datetime, tzinfo

    from ..repositories.external_calendar_event import ExternalCalendarEventRepository
    from ..repositories.patient import PatientRepository
    from ..repositories.patient_source_mapping import (
        PatientSourceMapping,
        PatientSourceMappingRepository,
    )
    from ..scheduling_engine.repositories.appointment import AppointmentRepository

logger = logging.getLogger(__name__)

#: What an appointment made for a followed session is called. The patient's
#: name is what the calendar shows; the event's own title is not copied.
APPOINTMENT_TITLE = "Session"

#: A chart that books without asking. Inactive and on-hold charts are asked.
ACTIVE = "active"

#: Refusing an answer that would settle every event under initials, or under a
#: name two charts share, at once. Those are answered one event at a time.
ONE_SESSION_AT_A_TIME = "Say which session this answer is for"

#: Refusing to remember a calendar's answer without knowing which calendar:
#: the row predates calendars being recorded and the main one is not known.
CALENDAR_NOT_KNOWN = "Which calendar this session is on isn't known yet; try again shortly"

#: What kind of thing an identifier is, which is what decides whether a
#: remembered answer for it may book without asking. See the module docstring.
IdentityKind = Literal["series", "slot", "initials", "name", "code"]


@dataclass
class Question:
    """One "who is this?": every open event with the same identifier, or one event."""

    source: str
    source_identifier: str
    title: str
    next_start_at: datetime
    recurring: bool
    match: MatchResult
    suggested_patient_id: str | None = None
    """A remembered or certain answer offered for confirmation rather than acted on."""
    outside_session_id: str | None = None
    """Set when the question is about this one event, because its identifier
    (initials, a name two charts share) could mean someone else next time."""
    client_inactive: bool = False
    """The suggested chart is inactive or on hold; confirming may reactivate it."""
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
    kind: IdentityKind
    scope: str | None
    """Whose answer this is remembered as; None when the calendar isn't known."""


class OutsideSessions:
    def __init__(
        self,
        events: ExternalCalendarEventRepository,
        appointments: AppointmentRepository,
        patients: PatientRepository,
        mappings: PatientSourceMappingRepository,
        *,
        zone: tzinfo = UTC,
        main_calendar_id: str | None = None,
    ) -> None:
        self._events = events
        self._appointments = appointments
        self._patients = patients
        self._mappings = mappings
        # The clinician's own zone: an event without a series is remembered
        # by the weekday and time it falls on there, as the import does.
        self._zone = zone
        # The clinician's main calendar, when the caller knows it: what a row
        # from before calendars were recorded is on, and where that
        # clinician's answers from before answers were the practice's belong.
        self._main_calendar_id = main_calendar_id

    @property
    def main_calendar_id(self) -> str | None:
        return self._main_calendar_id

    def in_zone(self, zone: tzinfo) -> OutsideSessions:
        """The same sessions, read in one clinician's zone."""
        return OutsideSessions(
            self._events,
            self._appointments,
            self._patients,
            self._mappings,
            zone=zone,
            main_calendar_id=self._main_calendar_id,
        )

    def with_main_calendar(self, calendar_id: str | None) -> OutsideSessions:
        """The same sessions, knowing which calendar is the clinician's main one."""
        return OutsideSessions(
            self._events,
            self._appointments,
            self._patients,
            self._mappings,
            zone=self._zone,
            main_calendar_id=calendar_id,
        )

    def context(self, user_id: str) -> MatchContext:
        return MatchContext.for_practice(
            user_id, self._patients, self._mappings, main_calendar_id=self._main_calendar_id
        )

    # --- The one booking rule ------------------------------------------------

    def unattended(self, row: ExternalCalendarEvent, ctx: MatchContext) -> str | None:
        """The chart this event may be booked to without asking, if any.

        A certain match is not enough: the identifier has to be one nobody
        else can turn up under, and the chart has to be active. See the
        module docstring for the rule per kind of identifier.
        """
        identity = self._identity(row)
        match = match_patient(identity.hint, ctx, name_alone_is_enough=identity.kind == "name")
        return self._unattended(row, identity, match, ctx)

    def dismissed(self, row: ExternalCalendarEvent, ctx: MatchContext) -> bool:
        """Whether the clinician already said this event's identifier is not a client.

        An identifier that is asked about every time settles nothing for the
        next event, so a not-a-client answer under it dismisses no one.
        """
        identity = self._identity(row)
        if self._asks_each_time(identity, ctx):
            return False
        return match_patient(identity.hint, ctx).evidence == "not_a_client"

    def _unattended(
        self,
        row: ExternalCalendarEvent,
        identity: _Identity,
        match: MatchResult,
        ctx: MatchContext,
    ) -> str | None:
        # Never for an identifier the clinician said is not a client, and
        # never onto a chart this clinician doesn't see: the row stays a
        # question, which says who does see that client.
        if match.evidence == "not_a_client" or (match.patient_id and not match.visible):
            return None
        patient_id = self._one_client(row, identity, match, ctx)
        if patient_id is None:
            return None
        patient = self._patients.get(patient_id, row.user_id)
        if patient is None or patient.status != ACTIVE:
            return None
        return patient_id

    def _one_client(
        self,
        row: ExternalCalendarEvent,
        identity: _Identity,
        match: MatchResult,
        ctx: MatchContext,
    ) -> str | None:
        """The one chart this identifier can only mean, if there is one."""
        if identity.kind == "name":
            # Exactly one of the clinician's own charts bears the name,
            # middle names aside, and any remembered answer agrees. A new
            # client with the same name taking over the slot before having
            # a chart is the residual case.
            # A remembered answer is read from the record, not the match:
            # one whose chart is gone yields no match at all, and must not
            # hand the name to whoever else bears it.
            assert identity.hint.full_name is not None  # noqa: S101 — a name kind has one
            bearing = [c.id for c in same_name_charts(identity.hint.full_name, ctx)]
            known = _known(ctx, identity)
            if len(bearing) != 1 or (known is not None and known.patient_id != bearing[0]):
                return None
            return bearing[0]
        if match.patient_id is None or match.evidence != "remembered":
            return None
        if identity.kind == "code":
            return match.patient_id
        if identity.kind == "series" and self._answered_under_this_title(row, identity, ctx):
            return match.patient_id
        # A retitled series, a slot, or initials: someone else's sooner or later.
        return None

    def _answered_under_this_title(
        self, row: ExternalCalendarEvent, identity: _Identity, ctx: MatchContext
    ) -> bool:
        """Whether a remembered series still carries the title it was answered under.

        An answer with no title on record (from before titles were kept) is
        asked about once, pre-filled; the answer records the title.
        """
        known = _known(ctx, identity)
        if known is None or known.answered_title is None:
            return False
        return known.answered_title == answered_title_digest(row.title)

    def _asks_each_time(self, identity: _Identity, ctx: MatchContext) -> bool:
        """Whether every event under this identifier is its own question."""
        if identity.kind == "initials":
            return True
        if identity.kind == "name":
            assert identity.hint.full_name is not None  # noqa: S101 — a name kind has one
            return len(same_name_charts(identity.hint.full_name, ctx)) > 1
        return False

    def asks_each_time(
        self, user_id: str, source: str, source_identifier: str, ctx: MatchContext | None = None
    ) -> bool:
        """Whether an answer under this identifier has to say which event it is for."""
        rows = self.open_rows(user_id, source, source_identifier)
        if not rows:
            return False
        return self._asks_each_time(self._identity(rows[0]), ctx or self.context(user_id))

    # --- Reading a calendar ------------------------------------------------

    def ingest_google(
        self, user_id: str, changes: list[dict[str, Any]], *, calendar_id: str | None = None
    ) -> Ingested:
        """Hold, refresh, answer or drop a row for each change on a followed calendar.

        ``calendar_id`` is the calendar the changes were read from; rows and
        the appointments booked for them record it. Moves and deletions of an
        answered session's appointment are the follower's, not this.
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
                calendar_id=calendar_id,
                start_at=start,
                end_at=end,
                title=str(change.get("summary") or ""),
            )
            result.held += self._place(incoming, row, ctx, result.booked)
        logger.info(
            "Followed %d outside sessions from a followed calendar, booked %d",
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
                row.appointment_id
                and incoming.calendar_id
                and incoming.calendar_id != row.calendar_id
            ):
                # An event can sit on two calendars under one id (an invited
                # copy of it). Read now from the one followed, it is that
                # calendar's, and so is the session following it.
                self._record_calendar(row.appointment_id, incoming.user_id, incoming.calendar_id)
        patient_id = self._unattended(incoming, identity, match, ctx)
        if patient_id is not None:
            if incoming.answer == ANSWER_OPEN:
                incoming.answer = ANSWER_CLIENT
                incoming.patient_id = patient_id
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

    def reconcile_full_read(
        self,
        user_id: str,
        present: set[str],
        window: tuple[datetime, datetime],
        calendar_id: str,
    ) -> list[dict[str, Any]]:
        """Catch up with a full read of the followed calendar.

        A full read (see ``MainCalendarRead``) holds every event in its
        window, and never reports what was deleted before it. So a row for an
        event in that window it doesn't hold is for an event that is gone, and
        goes. Nothing outside the window is judged, and nothing from another
        calendar: after the clinician chooses a different calendar, the
        sessions booked from the old one are not missing from the new one.
        An appointment following such an event is not cancelled here: it comes
        back as a deletion for the follower, so the bulk guard decides — a
        reset that seems to lose everything is held, never mass-cancelled.
        """
        start, end = window
        for row in self._events.list_by_source(user_id, GOOGLE_CALENDAR_SOURCE):
            in_window = row.end_at > start and row.start_at < end
            if row.calendar_id == calendar_id and in_window and row.source_event_id not in present:
                self._events.delete(user_id, row.id)
        return [
            {"google_event_id": appointment.outside_event_id, "status": "cancelled"}
            for appointment in self._appointments.list_by_range(user_id, start, end)
            if appointment.outside_source == GOOGLE_CALENDAR_SOURCE
            and appointment.outside_calendar_id == calendar_id
            and appointment.status == AppointmentStatus.CONFIRMED
            and appointment.outside_event_id not in present
        ]

    def claim_unrecorded(self, user_id: str, main_calendar_id: str) -> int:
        """Record the main calendar on rows and sessions from before calendars were recorded.

        Until a clinician could choose a calendar, only the main one was ever
        followed, so every Google row with no calendar came from it, and so
        did the appointment booked for it. Returns how many rows it recorded.
        """
        claimed = 0
        for row in self._unrecorded(user_id):
            row.calendar_id = main_calendar_id
            self._events.save(row)
            claimed += 1
        # Sessions, including any whose row an earlier read already removed.
        # Only upcoming ones matter: a read judges nothing before now.
        start = utc_now()
        for appointment in self._appointments.list_by_range(
            user_id, start, start + timedelta(days=MAX_HORIZON_DAYS)
        ):
            if (
                appointment.outside_source == GOOGLE_CALENDAR_SOURCE
                and appointment.outside_calendar_id is None
            ):
                appointment.outside_calendar_id = main_calendar_id
                self._keep_unless_booked_elsewhere(appointment)
        return claimed

    def has_unrecorded(self, user_id: str) -> bool:
        """Whether any followed row still has no calendar recorded on it."""
        return bool(self._unrecorded(user_id))

    def _unrecorded(self, user_id: str) -> list[ExternalCalendarEvent]:
        return [
            row
            for row in self._events.list_by_source(user_id, GOOGLE_CALENDAR_SOURCE)
            if row.calendar_id is None
        ]

    def _record_calendar(self, appointment_id: str, user_id: str, calendar_id: str) -> None:
        appointment = self._appointments.get(appointment_id, user_id)
        if appointment is not None and appointment.outside_calendar_id != calendar_id:
            appointment.outside_calendar_id = calendar_id
            self._keep_unless_booked_elsewhere(appointment)

    def _keep_unless_booked_elsewhere(self, appointment: Appointment) -> None:
        """Save a session's new calendar, unless a colleague already books that event there.

        Then this session is the second for one event, and recording the
        calendar would break the one-live-booking rule: it is left exactly as
        it was rather than failing the whole read, which would fail again on
        every read after.
        """
        try:
            self._appointments.update(appointment)
        except OutsideEventAlreadyBookedError:
            # HIPAA: nothing that identifies the session or its client.
            logger.warning("A session's event is already booked on that calendar; left as it was")

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
        """The questions still open, soonest first.

        One per identifier, or one per event when the identifier can't say
        which client the next event is (initials; a name two charts share).
        Whatever is known is offered as the answer to confirm, never acted on
        here: a remembered client, a unique name, an inactive chart's client.
        The clinician still says yes. A client of the practice the clinician
        doesn't see is never suggested; the question says who sees them.
        """
        ctx = self.context(user_id)
        by_key: dict[tuple[str, str, str | None], Question] = {}
        for row in self._events.list_open(user_id):
            if row.source in hidden:
                continue
            identity = self._identity(row)
            each_time = self._asks_each_time(identity, ctx)
            key = (row.source, identity.identifier, row.id if each_time else None)
            question = by_key.get(key)
            if question is None:
                question = self._question(row, identity, ctx, each_time=each_time)
                by_key[key] = question
            question.rows.append(row)
        return list(by_key.values())

    def _question(
        self, row: ExternalCalendarEvent, identity: _Identity, ctx: MatchContext, *, each_time: bool
    ) -> Question:
        if each_time:
            return self._each_time_question(row, identity, ctx)
        match = match_patient(identity.hint, ctx)
        if match.evidence is None and not match.patient_id and not match.possible_ids:
            # Remembered to a chart that is gone: the matcher rightly guesses
            # no one, but the question still offers whoever the title could
            # mean, none of them preselected.
            by_title = match_patient(_title_only(identity.hint), ctx)
            ids = [by_title.patient_id] if by_title.patient_id else by_title.possible_ids
            match = MatchResult(possible_ids=ids)
        suggested = None
        inactive = False
        if match.patient_id and match.visible:
            patient = self._patients.get(match.patient_id, row.user_id)
            inactive = patient is not None and patient.status != ACTIVE
            if match.evidence not in NAME_ONLY:
                # Remembered, or certain on more than a name, yet not booked:
                # offered preselected beside whoever else the title could
                # mean and "New client", so the clinician can say no. (A
                # name alone is already shown that way.)
                suggested = match.patient_id
                by_title = match_patient(
                    identity.hint.model_copy(update={"source_identifier": None}), ctx
                )
                others = [by_title.patient_id] if by_title.patient_id else by_title.possible_ids
                match = MatchResult(
                    possible_ids=[suggested, *(o for o in others if o != suggested)]
                )
        return Question(
            source=row.source,
            source_identifier=identity.identifier,
            title=row.title,
            next_start_at=row.start_at,
            recurring=bool(row.source_series_id),
            match=match,
            suggested_patient_id=suggested,
            outside_session_id=row.id if each_time else None,
            client_inactive=inactive,
        )

    def _each_time_question(
        self, row: ExternalCalendarEvent, identity: _Identity, ctx: MatchContext
    ) -> Question:
        """One event whose title can't say which client it is.

        Candidates come from the title alone, among the clinician's own
        charts. Whatever was answered last under the title is only the
        pre-fill, and only when the clinician still sees that chart: a
        remembered answer for initials is not an identity, so it can neither
        lock the question to a colleague's client nor stand for "not a
        client" on the next event.
        """
        by_title = match_patient(_title_only(identity.hint), ctx)
        candidates = [by_title.patient_id] if by_title.patient_id else by_title.possible_ids
        known = _known(ctx, identity)
        last = known.patient_id if known is not None else None
        suggested = last if last is not None and _sees(ctx, last) else None
        if suggested is None and by_title.patient_id and by_title.evidence not in NAME_ONLY:
            suggested = by_title.patient_id
        if suggested is not None:
            match = MatchResult(
                possible_ids=[suggested, *(c for c in candidates if c != suggested)]
            )
        else:
            match = by_title
        shown = suggested or match.patient_id
        patient = self._patients.get(shown, row.user_id) if shown else None
        return Question(
            source=row.source,
            source_identifier=identity.identifier,
            title=row.title,
            next_start_at=row.start_at,
            recurring=bool(row.source_series_id),
            match=match,
            suggested_patient_id=suggested,
            outside_session_id=row.id,
            client_inactive=patient is not None and patient.status != ACTIVE,
        )

    def open_rows(
        self, user_id: str, source: str, source_identifier: str, *, row_id: str | None = None
    ) -> list[ExternalCalendarEvent]:
        """The open rows one answer would settle: all under the identifier, or one."""
        return [
            row
            for row in self._events.list_by_source(user_id, source)
            if row.answer == ANSWER_OPEN
            and self._identity(row).identifier == source_identifier
            and (row_id is None or row.id == row_id)
        ]

    def seen_by_someone_else(
        self,
        user_id: str,
        source: str,
        source_identifier: str,
        ctx: MatchContext,
        *,
        row_id: str | None = None,
    ) -> bool:
        """Whether this question is certainly a client the clinician doesn't see.

        Such a question can't be answered with a new client, which would be a
        second chart for the same person, any more than with that chart.
        """
        rows = self.open_rows(user_id, source, source_identifier, row_id=row_id)
        if not rows:
            return False
        identity = self._identity(rows[0])
        hint = _title_only(identity.hint) if self._asks_each_time(identity, ctx) else identity.hint
        match = match_patient(hint, ctx)
        return match.patient_id is not None and not match.visible

    def answer(
        self,
        user_id: str,
        source: str,
        source_identifier: str,
        *,
        patient_id: str | None,
        ctx: MatchContext | None = None,
        row_id: str | None = None,
    ) -> list[ExternalCalendarEvent]:
        """Settle the open rows with this identifier, or one of them, and remember.

        ``patient_id`` None means not a client. Under an identifier that is
        asked about every time, that is one event's answer: the row is kept
        as not a client so it is not asked again, and nothing is remembered
        for the next. Otherwise it is remembered, and the rows go, leaving
        the events as busy blocks. A client books an appointment for each
        row that follows its event, and is remembered with the title it was
        confirmed under. Returns the rows answered.

        Raises ``ValueError`` for an answer under such an identifier that
        names no event: it would settle every event at once.
        """
        ctx = ctx or self.context(user_id)
        rows = self.open_rows(user_id, source, source_identifier, row_id=row_id)
        mapping_source = _mapping_source(source)
        scope = (
            self._identity(rows[0]).scope
            if rows
            else answer_scope(mapping_source, self._main_calendar_id, user_id)
        )
        each_time = bool(rows) and self._asks_each_time(self._identity(rows[0]), ctx)
        if each_time and row_id is None:
            raise ValueError(ONE_SESSION_AT_A_TIME)
        if patient_id is None:
            if each_time:
                for row in rows:
                    row.answer = ANSWER_NOT_A_CLIENT
                    self._events.save(row)
                return rows
            if scope is None:
                raise ValueError(CALENDAR_NOT_KNOWN)
            remember_not_a_client(mapping_source, source_identifier, ctx, scope=scope)
            for row in rows:
                self._events.delete(user_id, row.id)
            return rows
        if scope is None:
            raise ValueError(CALENDAR_NOT_KNOWN)
        remember_match(
            mapping_source,
            source_identifier,
            patient_id,
            ctx,
            scope=scope,
            # The title the question showed: the soonest row's.
            answered_title=(
                answered_title_digest(min(rows, key=lambda r: r.start_at).title) if rows else None
            ),
        )
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

            named = feed_identity(feed, row.title, row.user_id)
            return _Identity(feed, named.identifier, named.hint, named.kind, named.hint.scope)
        identifier = event_source_identifier(
            row.source_series_id, row.title, row.start_at, self._zone
        )
        # The calendar the row was read from; a row from before calendars were
        # recorded came from the main one.
        scope = answer_scope(row.source, row.calendar_id or self._main_calendar_id, row.user_id)
        return _Identity(
            row.source,
            identifier,
            PatientHint(
                full_name=row.title, source=row.source, source_identifier=identifier, scope=scope
            ),
            "series" if identifier.startswith(SERIES_PREFIX) else "slot",
            scope,
        )

    def _book(self, row: ExternalCalendarEvent) -> Appointment | None:
        """Make the appointment an answered row follows, or link to the one already made.

        One outside event is at most one live appointment in the practice.
        A colleague following the same calendar may have answered first, so
        the row links to their appointment rather than making another; and
        when two requests race, the database's unique index refuses the
        second, which then links the same way.

        Skipped when something else is already booked over it — most often
        the same session booked in Pablo as well — so the practice isn't
        double booked. The row is still answered, and so never asked about again.
        """
        if row.patient_id is None or row.appointment_id is not None:
            return None
        already = self._already_booked(row)
        if already is not None:
            row.appointment_id = already
            return None
        if self._appointments.list_overlapping(row.user_id, row.start_at, row.end_at):
            logger.info("An answered outside session overlaps a booking; not booked twice")
            return None
        feed = ical_feed(row.source)
        now = utc_now()
        try:
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
                    outside_calendar_id=row.calendar_id,
                    # A feed's own sync follows these by uid, as it does the
                    # sessions it matched itself.
                    ical_uid=row.source_event_id if feed else None,
                    ical_source=feed,
                    ical_sync_status="synced" if feed else None,
                    created_at=now,
                    updated_at=now,
                )
            )
        except OutsideEventAlreadyBookedError:
            logger.info("An outside session was booked by another request first; linked to it")
            row.appointment_id = self._already_booked(row)
            return None
        row.appointment_id = appointment.id
        return appointment

    def _already_booked(self, row: ExternalCalendarEvent) -> str | None:
        return self._appointments.outside_appointment_id(
            row.source, row.calendar_id, row.source_event_id, row.user_id
        )


def _remembered(ctx: MatchContext, identity: _Identity) -> bool:
    return _known(ctx, identity) is not None


def _known(ctx: MatchContext, identity: _Identity) -> PatientSourceMapping | None:
    """The answer on record under this identifier, whatever became of its chart."""
    if identity.scope is None:
        return None
    return ctx.lookup(identity.mapping_source, identity.scope, identity.identifier)


def _title_only(hint: PatientHint) -> PatientHint:
    """What the title says, without what was remembered under it."""
    return hint.model_copy(update={"source_identifier": None})


def _sees(ctx: MatchContext, patient_id: str) -> bool:
    candidate = ctx.candidate(patient_id)
    return candidate is not None and candidate.visible


def _mapping_source(source: str) -> str:
    """Where an answer for this source is remembered.

    A feed keeps the name it has always remembered its clients under, so
    answers given before it was followed still count.
    """
    return ical_feed(source) or source


__all__ = [
    "ACTIVE",
    "APPOINTMENT_TITLE",
    "CALENDAR_NOT_KNOWN",
    "ONE_SESSION_AT_A_TIME",
    "Ingested",
    "OutsideSessions",
    "Question",
]
