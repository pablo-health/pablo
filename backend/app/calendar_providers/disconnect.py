# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What disconnecting Google Calendar removes besides the tokens.

Everything Pablo read from the calendar goes: the events it followed,
asked about or answered (``external_calendar_events``), and every answer the
clinician gave about them (``patient_source_mappings`` under the Google
source). That includes answers on a calendar shared with colleagues, which a
colleague who still follows it is asked again; an answer a colleague gave
stays. A clinician who disconnects reasonably expects Pablo to stop holding
what it read from their calendar.

Pablo's own records stay, untouched. An appointment booked in Pablo —
including one booked from an answered outside session — belongs to the
clinician, with whatever session and note hang off it. It keeps the id of
the event it is tied to: the one Pablo wrote, or the one it follows. An id
is all that is kept of either event, and it is what lets a later connection
pick the appointment back up — updating the event Pablo wrote rather than
writing a duplicate, and re-linking a followed session when its series is
answered again rather than leaving it never to follow Google again.

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


@dataclass(frozen=True)
class Forgotten:
    """How much went. Counts only: this is what reaches the audit trail."""

    calendar_events_deleted: int
    remembered_answers_deleted: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def forget_google_calendar(
    user_id: str,
    *,
    events: ExternalCalendarEventRepository,
    mappings: PatientSourceMappingRepository,
) -> Forgotten:
    """Remove what Pablo read from this clinician's Google Calendar."""
    return Forgotten(
        calendar_events_deleted=events.delete_by_source(user_id, GOOGLE_CALENDAR_SOURCE),
        remembered_answers_deleted=mappings.forget_answers_by(user_id, GOOGLE_CALENDAR_SOURCE),
    )
