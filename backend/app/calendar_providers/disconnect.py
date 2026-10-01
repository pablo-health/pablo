# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What disconnecting Google Calendar removes besides the tokens.

Everything Pablo read from the calendar goes: the events it followed,
asked about or answered (``external_calendar_events``), and the answers it
remembered for them (``patient_source_mappings`` under the Google source).
A clinician who disconnects reasonably expects Pablo to stop holding what it
read from their calendar.

Pablo's own records stay. An appointment booked in Pablo — including one
booked from an answered outside session — belongs to the clinician, with
whatever session and note hang off it. Only its pointers into Google go,
since an event id means nothing once the connection is gone.

Followed calendar feeds are separate connections with their own sources,
and the id of the calendar Pablo made is kept so a reconnect reuses it
(``GoogleCalendarService.disconnect``).

All of this runs in the caller's session, so it commits or rolls back with
the token deletion.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from .source_identity import GOOGLE_CALENDAR_SOURCE

if TYPE_CHECKING:
    from ..repositories.external_calendar_event import ExternalCalendarEventRepository
    from ..repositories.patient_source_mapping import PatientSourceMappingRepository
    from ..scheduling_engine.repositories.appointment import AppointmentRepository


@dataclass(frozen=True)
class Forgotten:
    """How much went. Counts only: this is what reaches the audit trail."""

    calendar_events_deleted: int
    remembered_answers_deleted: int
    appointments_unlinked: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def forget_google_calendar(
    user_id: str,
    *,
    events: ExternalCalendarEventRepository,
    mappings: PatientSourceMappingRepository,
    appointments: AppointmentRepository,
) -> Forgotten:
    """Remove what Pablo read from this clinician's Google Calendar."""
    return Forgotten(
        calendar_events_deleted=events.delete_by_source(user_id, GOOGLE_CALENDAR_SOURCE),
        remembered_answers_deleted=mappings.delete_by_source(user_id, GOOGLE_CALENDAR_SOURCE),
        appointments_unlinked=appointments.clear_google_calendar_links(
            user_id, GOOGLE_CALENDAR_SOURCE
        ),
    )
