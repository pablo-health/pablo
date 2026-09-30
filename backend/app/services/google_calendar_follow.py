# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Follow what the therapist does in Google Calendar to the sessions Pablo put there.

A session Pablo pushed to Google and then saw moved or deleted there was
almost certainly moved or deleted by the therapist. Leaving Pablo at the old
time means reminders and the client's own view say one thing while the
therapist's calendar says another, so the time and the existence of such a
session follow Google. Nothing else does: the title, attendees and description
of a Google event never flow back.

The guards are what make that safe to do without asking:

* **Only Pablo's own events**, for confirmed appointments.
* **Never the past, never a session with a note** — those are a record.
* **No double booking.** A move onto another of the clinician's sessions is
  not followed; Pablo's time stays and is flagged (``external_change``).
* **A deletion is a quiet cancellation** — no client message, no late fee —
  and the therapist can undo it.
* **Bulk deletions are held.** A poll that would cancel more than
  :data:`BULK_MIN_REMOVALS` sessions and more than :data:`BULK_MIN_SHARE` of
  the upcoming ones cancels none: that looks like an accident, and it is the
  therapist's to confirm.
* **A deleted calendar cancels nothing.** It is made again and the upcoming
  sessions pushed back into it (see the sync scheduler).

The same guards apply to the sessions another service puts on the
clinician's main calendar once they are answered (``outside_sessions``): those
appointments follow their event's time and existence the same way, except
that Pablo never writes to an event it did not create — keeping Pablo's side
of a change never pushes anything back.

Every automatic move and cancellation is audited as the system acting for
Google Calendar sync. HIPAA: logs carry counts only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from ..models.audit import ACTOR_TYPE_SYSTEM, AuditAction
from ..scheduling_engine.exceptions import AppointmentConflictError
from ..scheduling_engine.models.appointment import (
    AppointmentStatus,
    CancellationActor,
    ChangeRecord,
)
from ..scheduling_engine.services.scheduling import SchedulingService
from ..utcnow import utc_now
from .google_calendar_service import parse_event_time
from .telehealth import GOOGLE_MEET

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..models import User
    from ..scheduling_engine.models.appointment import Appointment
    from ..scheduling_engine.repositories.appointment import AppointmentRepository
    from .audit_service import AuditService
    from .google_calendar_service import GoogleCalendarService

logger = logging.getLogger(__name__)


class GoogleSyncStatus(StrEnum):
    """Values of ``appointments.google_sync_status``."""

    SYNCED = "synced"
    ERROR = "error"
    #: Moved in Google to a time Pablo could not follow; Pablo kept its own.
    EXTERNAL_CHANGE = "external_change"
    #: Deleted in Google, so cancelled here. Undoable.
    REMOVED_IN_GOOGLE = "removed_in_google"
    #: Deleted in Google as part of a bulk removal; still booked here, held
    #: for the therapist to confirm or put back.
    MISSING_IN_GOOGLE = "missing_in_google"


class Resolution(StrEnum):
    """How the therapist settles a change Pablo did not follow on its own."""

    KEEP_PABLO = "keep_pablo"
    ACCEPT_GOOGLE = "accept_google"


#: What the audit trail names as the actor's component.
SYNC_COMPONENT = "google_calendar_sync"
MOVED_IN_GOOGLE = "moved_in_google_calendar"
REMOVED_FROM_GOOGLE = "removed_from_google_calendar"

BULK_MIN_REMOVALS = 3
BULK_MIN_SHARE = 0.25

#: How far ahead "upcoming" reaches, for the bulk guard and for re-pushing a
#: recreated calendar. Generous on purpose: a recurring series is materialised
#: well ahead, and every one of those occurrences is on the calendar.
UPCOMING_HORIZON = timedelta(days=730)


@dataclass
class FollowSummary:
    """What one poll did to the clinician's sessions."""

    moved: int = 0
    cancelled: int = 0
    flagged: int = 0
    held: int = 0

    @property
    def changed(self) -> int:
        return self.moved + self.cancelled + self.flagged + self.held


@dataclass
class _Move:
    appointment: Appointment
    start: datetime
    end: datetime


def _pablos_own(appointment: Appointment) -> bool:
    return bool(appointment.google_event_id)


def _following(source: str) -> Callable[[Appointment], bool]:
    return lambda appointment: appointment.outside_source == source


def _on_google(appointment: Appointment) -> bool:
    return bool(appointment.google_event_id or appointment.outside_event_id)


class GoogleChangeFollower:
    """Applies Google-side moves and deletions to the sessions Pablo follows.

    Pablo's own pushed events by default; ``outside_source`` switches to the
    appointments made for sessions on another service's events.
    """

    def __init__(
        self,
        appointment_repo: AppointmentRepository,
        calendar: GoogleCalendarService,
    ) -> None:
        self._repo = appointment_repo
        self._calendar = calendar
        # No availability engine: the therapist chose the new time in their
        # own calendar, so working-hours rules are theirs to have broken. A
        # collision with another session is still refused — that is a
        # double booking, not a preference.
        self._scheduling = SchedulingService(appointment_repo)

    # --- Inbound -----------------------------------------------------------

    def follow(
        self,
        user: User,
        audit: AuditService,
        changes: list[dict[str, Any]],
        *,
        outside_source: str | None = None,
    ) -> FollowSummary:
        """Apply one poll's worth of Google changes.

        ``outside_source`` follows the appointments made for another
        service's events from that source instead of Pablo's own.
        """
        summary = FollowSummary()
        now = utc_now()
        moves: list[_Move] = []
        removals: list[Appointment] = []
        tracks = _following(outside_source) if outside_source else _pablos_own

        for change in changes:
            appointment = self._followable(user.id, change, now, outside_source)
            if appointment is None:
                continue
            if change.get("status") == "cancelled":
                removals.append(appointment)
                continue
            start = parse_event_time(change.get("start") or {})
            end = parse_event_time(change.get("end") or {})
            if start is None or end is None:
                # Turned into an all-day event: no session time to follow.
                self._set_status(appointment, GoogleSyncStatus.EXTERNAL_CHANGE)
                summary.flagged += 1
                continue
            if start == appointment.start_at and end == appointment.end_at:
                # Pablo's own push coming back, or Google put back where Pablo
                # has it — either way whatever was flagged is settled.
                if appointment.google_sync_status != GoogleSyncStatus.SYNCED:
                    self._set_status(appointment, GoogleSyncStatus.SYNCED)
                continue
            moves.append(_Move(appointment, start, end))

        # Deletions first: a slot they free may be exactly where a move in the
        # same poll is going.
        if self._is_bulk_removal(user.id, removals, now, tracks):
            for appointment in removals:
                self._set_status(appointment, GoogleSyncStatus.MISSING_IN_GOOGLE)
            summary.held = len(removals)
            logger.warning("Held %d sessions deleted from Google Calendar at once", len(removals))
        else:
            for appointment in removals:
                self._quiet_cancel(user, audit, appointment)
            summary.cancelled = len(removals)

        blocked = self._apply_moves(user, audit, moves)
        for move in blocked:
            self._set_status(move.appointment, GoogleSyncStatus.EXTERNAL_CHANGE)
        summary.moved = len(moves) - len(blocked)
        summary.flagged += len(blocked)
        logger.info(
            "Followed Google Calendar: moved=%d cancelled=%d flagged=%d held=%d",
            summary.moved,
            summary.cancelled,
            summary.flagged,
            summary.held,
        )
        return summary

    def _followable(
        self, user_id: str, change: dict[str, Any], now: datetime, outside_source: str | None
    ) -> Appointment | None:
        event_id = change.get("google_event_id")
        if not event_id:
            return None
        appointment = (
            self._repo.get_by_outside_event(user_id, outside_source, str(event_id))
            if outside_source
            else self._repo.get_by_google_event_id(user_id, str(event_id))
        )
        if appointment is None or appointment.status != AppointmentStatus.CONFIRMED:
            return None
        if appointment.session_id or appointment.start_at <= now:
            return None
        return appointment

    def _is_bulk_removal(
        self,
        user_id: str,
        removals: list[Appointment],
        now: datetime,
        tracks: Callable[[Appointment], bool],
    ) -> bool:
        if len(removals) <= BULK_MIN_REMOVALS:
            return False
        upcoming = len(self._upcoming(user_id, now, tracks))
        return len(removals) > BULK_MIN_SHARE * upcoming

    def _apply_moves(self, user: User, audit: AuditService, moves: list[_Move]) -> list[_Move]:
        """Follow each move, retrying the collisions until a pass moves nothing.

        The retry is for a chain rearranged in one go — B moved into A's slot,
        A moved later — where the order Google reported them in decides which
        collides first. A swap collides both ways and stays blocked. Returns
        the moves that could not be followed.
        """
        pending = moves
        while True:
            blocked = [move for move in pending if not self._try_move(user, audit, move)]
            if not blocked or len(blocked) == len(pending):
                return blocked
            pending = blocked

    def _try_move(self, user: User, audit: AuditService, move: _Move) -> bool:
        try:
            appointment = self._move_to(user.id, move.appointment.id, move.start, move.end)
        except AppointmentConflictError:
            return False
        audit.log_appointment_action(
            AuditAction.APPOINTMENT_UPDATED,
            user,
            None,
            appointment.id,
            patient_id=appointment.patient_id,
            changes={"changed_fields": ["end_at", "start_at"], "reason": MOVED_IN_GOOGLE},
            actor_type=ACTOR_TYPE_SYSTEM,
            actor_component=SYNC_COMPONENT,
        )
        return True

    def _move_to(
        self, user_id: str, appointment_id: str, start: datetime, end: datetime
    ) -> Appointment:
        """Move through the ordinary edit path, which refuses a collision.

        The collision is checked here first as well, so a refused move leaves
        nothing half-applied to the appointment a caller may still hold.
        """
        if self._repo.list_overlapping(user_id, start, end, exclude_appointment_id=appointment_id):
            raise AppointmentConflictError("Conflicts with an existing appointment")
        appointment = self._scheduling.update_appointment(
            appointment_id,
            user_id,
            start_at=start,
            end_at=end,
            duration_minutes=int((end - start).total_seconds() // 60),
            google_sync_status=GoogleSyncStatus.SYNCED,
        )
        # Reminders already sent were for the old time.
        appointment.reminder_24h_sent = False
        appointment.reminder_1h_sent = False
        return self._repo.update(appointment)

    def _quiet_cancel(self, user: User, audit: AuditService, appointment: Appointment) -> None:
        """Cancel without telling the client and without a fee.

        The system is the actor, which chargeable-cancellation logic treats as
        nobody, and ``late`` is stated as False rather than left for a later
        reader to derive from the clock.
        """
        cancelled = self._scheduling.cancel_appointment(
            appointment.id,
            user.id,
            record=ChangeRecord(by=CancellationActor.SYSTEM, late=False),
        )
        self._set_status(cancelled, GoogleSyncStatus.REMOVED_IN_GOOGLE)
        audit.log_appointment_action(
            AuditAction.APPOINTMENT_CANCELLED,
            user,
            None,
            cancelled.id,
            patient_id=cancelled.patient_id,
            changes={"reason": REMOVED_FROM_GOOGLE},
            actor_type=ACTOR_TYPE_SYSTEM,
            actor_component=SYNC_COMPONENT,
        )

    # --- The calendar itself ------------------------------------------------

    def repush_upcoming(self, user_id: str) -> int:
        """Put every upcoming session into a freshly made calendar.

        Each gets a new event: the old ids belonged to the deleted calendar.
        Returns how many were pushed.
        """
        return sum(
            self._push_new_event(user_id, appointment).google_sync_status == GoogleSyncStatus.SYNCED
            for appointment in self._upcoming(user_id, utc_now(), _pablos_own)
        )

    # --- The therapist settling a change -----------------------------------

    def resolve(self, user_id: str, appointment_id: str, resolution: Resolution) -> Appointment:
        """Settle one flagged session the way the therapist chose.

        ``keep_pablo`` undoes a quiet cancellation or writes Pablo's time back
        over a move; ``accept_google`` accepts the cancellation or takes
        Google's time. A session followed from another service's event keeps
        Pablo's side without writing anything back: that event is not Pablo's.
        """
        appointment = self._scheduling.get_appointment(appointment_id, user_id)
        keep = resolution is Resolution.KEEP_PABLO
        outside = appointment.outside_event_id is not None
        if appointment.google_sync_status == GoogleSyncStatus.REMOVED_IN_GOOGLE:
            if keep:
                restored = self._scheduling.restore_appointment(appointment_id, user_id)
                if outside:
                    return self._settled(restored)
                return self._push_new_event(user_id, restored)
            return self._settled(appointment)  # accepted; stops asking to be undone
        if appointment.google_sync_status == GoogleSyncStatus.EXTERNAL_CHANGE:
            if keep:
                return self._settled(appointment) if outside else self._push(user_id, appointment)
            event_id = appointment.outside_event_id or appointment.google_event_id
            times = (
                self._calendar.read_event_times(user_id, event_id, main_calendar=outside)
                if event_id
                else None
            )
            if times is None:
                raise GoogleChangeUnavailableError(appointment_id)
            return self._move_to(user_id, appointment_id, *times)
        raise GoogleChangeUnavailableError(appointment_id)

    def held_removals(self, user_id: str) -> list[Appointment]:
        """Sessions a bulk deletion left waiting on the therapist."""
        return [
            appointment
            for appointment in self._upcoming(user_id, utc_now(), _on_google)
            if appointment.google_sync_status == GoogleSyncStatus.MISSING_IN_GOOGLE
        ]

    def resolve_held(self, user_id: str, resolution: Resolution) -> list[Appointment]:
        """Settle a held bulk deletion all at once: push them back, or cancel them.

        A held session followed from another service's event stays booked
        without being pushed anywhere: that event is not Pablo's to recreate.
        """
        held = self.held_removals(user_id)
        if resolution is Resolution.KEEP_PABLO:
            return [
                self._settled(appointment)
                if appointment.outside_event_id
                else self._push_new_event(user_id, appointment)
                for appointment in held
            ]
        cancelled: list[Appointment] = []
        for appointment in held:
            row = self._scheduling.cancel_appointment(
                appointment.id,
                user_id,
                record=ChangeRecord(by=CancellationActor.CLINICIAN, by_id=user_id, late=False),
            )
            # Decided by the therapist just now, so nothing left to undo.
            row.google_sync_status = None
            cancelled.append(self._repo.update(row))
        return cancelled

    # --- Helpers ------------------------------------------------------------

    def _upcoming(
        self, user_id: str, now: datetime, tracks: Callable[[Appointment], bool]
    ) -> list[Appointment]:
        return [
            appointment
            for appointment in self._repo.list_by_range(user_id, now, now + UPCOMING_HORIZON)
            if appointment.status == AppointmentStatus.CONFIRMED and tracks(appointment)
        ]

    def _set_status(self, appointment: Appointment, status: GoogleSyncStatus) -> None:
        appointment.google_sync_status = status
        self._repo.update(appointment)

    def _settled(self, appointment: Appointment) -> Appointment:
        """Nothing left to ask about, and nothing written anywhere else."""
        appointment.google_sync_status = None
        return self._repo.update(appointment)

    def _push_new_event(self, user_id: str, appointment: Appointment) -> Appointment:
        """Push as a new event. The old one is gone, so updating it would fail."""
        appointment.google_event_id = None
        return self._push(user_id, appointment)

    def _push(self, user_id: str, appointment: Appointment) -> Appointment:
        """Best-effort push, recording how it went on the appointment.

        Mirrors the push after an in-app edit: a Google failure is recorded as
        ``error`` rather than raised, because the appointment itself is fine.
        """
        try:
            pushed = self._calendar.push_appointment_event(user_id, appointment)
        except Exception:
            logger.exception("Failed to push appointment to Google Calendar")
            appointment.google_sync_status = GoogleSyncStatus.ERROR
            return self._repo.update(appointment)
        if pushed is None:
            # Disconnected since: there is no calendar left to agree with.
            appointment.google_event_id = None
            appointment.google_sync_status = None
            return self._repo.update(appointment)
        appointment.google_event_id = pushed.event_id
        appointment.google_sync_status = GoogleSyncStatus.SYNCED
        if pushed.conference_url and appointment.provider == GOOGLE_MEET:
            appointment.video_link = pushed.conference_url
        return self._repo.update(appointment)


class GoogleChangeUnavailableError(Exception):
    """The appointment has no Google change of that kind waiting to be settled."""

    def __init__(self, appointment_id: str) -> None:
        super().__init__(f"Appointment {appointment_id} has no Google Calendar change to settle")
