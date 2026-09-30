# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Sessions on a calendar Pablo follows, and what the clinician said about them.

Each row is one event another service put on a calendar Pablo reads: open
until the clinician says who it is, then a client (with the appointment made
for it) or not a client. See ``app.services.outside_sessions``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..utcnow import utc_now

if TYPE_CHECKING:
    from datetime import datetime

#: Nobody has said who this is yet.
ANSWER_OPEN = "open"
#: A client; ``patient_id`` says which and ``appointment_id`` follows the event.
ANSWER_CLIENT = "client"
#: Not a client. Only kept until the event is next seen; it is never asked about.
ANSWER_NOT_A_CLIENT = "not_a_client"


@dataclass
class ExternalCalendarEvent:
    id: str
    user_id: str
    source: str
    source_event_id: str
    start_at: datetime
    end_at: datetime
    title: str = ""
    source_series_id: str | None = None
    calendar_id: str | None = None
    """The followed calendar the event is on; None for a feed."""
    answer: str = ANSWER_OPEN
    patient_id: str | None = None
    appointment_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


class ExternalCalendarEventRepository(ABC):
    """Storage for followed calendar events, per clinician."""

    @abstractmethod
    def get(self, user_id: str, source: str, source_event_id: str) -> ExternalCalendarEvent | None:
        pass

    @abstractmethod
    def get_by_id(self, user_id: str, event_id: str) -> ExternalCalendarEvent | None:
        pass

    @abstractmethod
    def list_by_source(self, user_id: str, source: str) -> list[ExternalCalendarEvent]:
        pass

    @abstractmethod
    def list_open(
        self, user_id: str, start: datetime | None = None, end: datetime | None = None
    ) -> list[ExternalCalendarEvent]:
        """Open rows, soonest first; within ``[start, end)`` when given."""

    @abstractmethod
    def save(self, event: ExternalCalendarEvent) -> None:
        """Insert, or replace the row with the same id."""

    @abstractmethod
    def delete(self, user_id: str, event_id: str) -> None:
        pass


class InMemoryExternalCalendarEventRepository(ExternalCalendarEventRepository):
    """In-memory implementation for tests."""

    def __init__(self) -> None:
        self._events: dict[str, ExternalCalendarEvent] = {}

    def get(self, user_id: str, source: str, source_event_id: str) -> ExternalCalendarEvent | None:
        return next(
            (
                e
                for e in self._events.values()
                if (e.user_id, e.source, e.source_event_id) == (user_id, source, source_event_id)
            ),
            None,
        )

    def get_by_id(self, user_id: str, event_id: str) -> ExternalCalendarEvent | None:
        event = self._events.get(event_id)
        return event if event is not None and event.user_id == user_id else None

    def list_by_source(self, user_id: str, source: str) -> list[ExternalCalendarEvent]:
        return [e for e in self._events.values() if e.user_id == user_id and e.source == source]

    def list_open(
        self, user_id: str, start: datetime | None = None, end: datetime | None = None
    ) -> list[ExternalCalendarEvent]:
        found = [
            e
            for e in self._events.values()
            if e.user_id == user_id
            and e.answer == ANSWER_OPEN
            and (start is None or e.end_at > start)
            and (end is None or e.start_at < end)
        ]
        return sorted(found, key=lambda e: e.start_at)

    def save(self, event: ExternalCalendarEvent) -> None:
        self._events[event.id] = event

    def delete(self, user_id: str, event_id: str) -> None:
        if self.get_by_id(user_id, event_id) is not None:
            del self._events[event_id]
