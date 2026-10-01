# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Changes in Google Calendar that Pablo left for the clinician to settle.

The sync follows most of what a clinician does to their sessions in Google
Calendar on its own (:mod:`app.services.google_calendar_follow`). Three
cases it leaves standing, marked on the appointment, and those are the
items here:

* a move it could not follow, so Pablo kept its time (``external_change``);
* a deletion it followed by cancelling quietly, still undoable
  (``removed_in_google``);
* a deletion held back because many sessions went at once
  (``missing_in_google``).

Only upcoming sessions: once a session's time has passed, there is nothing
left to keep, undo or move. Settling one — Keep, Use Google's time, Undo, or
accepting the cancellation — clears the mark, which takes the item out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...models.inbox import KIND_CALENDAR_CHANGE, InboxItem
from ...repositories import get_appointment_repository, get_patient_repository
from ...services.google_calendar_follow import GoogleSyncStatus
from ._ids import uuids

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from ...repositories import PatientRepository
    from ...scheduling_engine.models.appointment import Appointment
    from ...scheduling_engine.repositories.appointment import AppointmentRepository
    from ..registry import InboxContext

NEEDS_CLINICIAN = (
    GoogleSyncStatus.EXTERNAL_CHANGE,
    GoogleSyncStatus.REMOVED_IN_GOOGLE,
    GoogleSyncStatus.MISSING_IN_GOOGLE,
)

# The same words the calendar's own notices use for each case.
_TITLES: dict[str, str] = {
    GoogleSyncStatus.EXTERNAL_CHANGE: (
        "Google Calendar has a different time for this session, and it couldn't move there."
    ),
    GoogleSyncStatus.REMOVED_IN_GOOGLE: "Removed from Google Calendar, so cancelled here.",
    GoogleSyncStatus.MISSING_IN_GOOGLE: (
        "Removed from Google Calendar with many others, so it's still booked here."
    ),
}


class CalendarChangeSource:
    kind = KIND_CALENDAR_CHANGE

    def __init__(
        self,
        appointments: Callable[[], AppointmentRepository] = get_appointment_repository,
        patients: Callable[[], PatientRepository] = get_patient_repository,
    ) -> None:
        self._appointments = appointments
        self._patients = patients

    def _items(self, ctx: InboxContext, appointments: list[Appointment]) -> list[InboxItem]:
        names = self._patients().get_multiple(
            list({a.patient_id for a in appointments if a.patient_id}), ctx.user_id
        )
        items: list[InboxItem] = []
        for appointment in appointments:
            status = appointment.google_sync_status or ""
            patient = names.get(appointment.patient_id)
            items.append(
                InboxItem(
                    kind=KIND_CALENDAR_CHANGE,
                    source_id=appointment.id,
                    patient_id=appointment.patient_id or None,
                    patient_name=patient.display_name if patient else appointment.title,
                    title=_TITLES.get(status, "Changed in Google Calendar."),
                    occurred_at=appointment.updated_at or appointment.start_at,
                    href="/dashboard/calendar",
                    context={
                        "appointment_id": appointment.id,
                        "google_sync_status": status,
                        "start_at": appointment.start_at.isoformat(),
                    },
                )
            )
        return items

    def list_open(self, ctx: InboxContext) -> list[InboxItem]:
        appointments = self._appointments().list_by_google_sync_status(
            ctx.user_id, NEEDS_CLINICIAN, starting_after=ctx.now
        )
        return self._items(ctx, appointments)

    def get_items(self, ctx: InboxContext, source_ids: Iterable[str]) -> list[InboxItem]:
        repo = self._appointments()
        appointments = [
            appointment
            for appointment_id in uuids(source_ids)
            if (appointment := repo.get(appointment_id, ctx.user_id)) is not None
        ]
        return self._items(ctx, appointments)
